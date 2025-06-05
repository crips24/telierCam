import os
import asyncio
import json
from aiohttp import web, WSMsgType
import bleach
from dotenv import load_dotenv

load_dotenv()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD must be set in .env")

feeds = {}
feed_counter = 0

chat_clients = {}
admin_clients = set()
chat_history = []
banned_ips = set()

CHAT_HISTORY_LEN = 50

routes = web.RouteTableDef()

@routes.get('/')
async def index(request):
    return web.FileResponse('index.html')

@routes.get('/add_camera')
async def add_camera_page(request):
    return web.FileResponse('add_camera.html')

@routes.get('/ws_stream')
async def ws_stream(request):
    ws = web.WebSocketResponse(max_msg_size=10**8)
    await ws.prepare(request)

    ip = request.remote
    if ip in banned_ips:
        await ws.close()
        return ws

    role = request.query.get('role')
    fid = request.query.get('feed_id')
    try:
        fid = int(fid)
    except:
        await ws.close()
        return ws

    if fid not in feeds:
        await ws.close()
        return ws

    entry = feeds[fid]

    if role == 'broadcaster':
        # Kick out the old broadcaster if one exists
        old = entry.get('broadcaster')
        if old:
            try:
                await old.close()
            except:
                pass

        entry['broadcaster'] = ws

        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    # Safely send this frame to every viewer
                    for v in list(entry['viewers']):
                        if v.closed:
                            entry['viewers'].discard(v)
                            continue
                        try:
                            await v.send_bytes(msg.data)
                        except Exception:
                            # That viewer is dead; close and remove
                            try:
                                await v.close()
                            except:
                                pass
                            entry['viewers'].discard(v)
            # end async for
        finally:
            # Broadcaster is gone—clear the slot
            entry['broadcaster'] = None

    else:
        # Role = viewer
        entry['viewers'].add(ws)
        try:
            async for _ in ws:
                pass
        finally:
            entry['viewers'].discard(ws)

    return ws


@routes.get('/ws_chat')
async def ws_chat(request):
    global feed_counter
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    ip = request.remote
    if ip in banned_ips:
        await ws.close()
        return ws

    chat_clients[ws] = ip

    feed_list = [
        {'feed_id': fid, 'name': feeds[fid]['name']}
        for fid in feeds
    ]
    await ws.send_json({'action': 'initial_feeds', 'feeds': feed_list})
    await ws.send_json({'action': 'chat_history', 'history': chat_history})
    await broadcast({'action': 'viewer_count', 'count': len(chat_clients)})

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
        except:
            continue

        action = data.get('action')

        if action == 'login':
            if data.get('password') == ADMIN_PASSWORD:
                admin_clients.add(ws)
                await ws.send_json({'action': 'login', 'success': True})
            else:
                await ws.send_json({'action': 'login', 'success': False})

        elif action == 'create_feed' and ws in admin_clients:
            name = bleach.clean(data.get('name', '').strip(), strip=True)
            fid = feed_counter
            feed_counter += 1
            feeds[fid] = {
                'name': name,
                'broadcaster': None,
                'viewers': set()
            }
            await broadcast({'action': 'feed_created', 'feed_id': fid, 'name': name})

        elif action == 'remove_feed' and ws in admin_clients:
            fid = data.get('feed_id')
            feed = feeds.get(fid)
            if not feed:
                continue

            b = feed.get('broadcaster')
            if b:
                try:
                    await b.close()
                except:
                    pass

            for v in list(feed.get('viewers', [])):
                try:
                    await v.close()
                except:
                    pass

            del feeds[fid]
            await broadcast({'action': 'feed_removed', 'feed_id': fid})

        elif action == 'chat':
            if ip in banned_ips:
                continue
            text = data.get('msg', '').strip()
            if not text:
                continue

            if text.startswith('/ban ') and ws in admin_clients:
                target = text.split(' ', 1)[1].strip()
                banned_ips.add(target)
                for c, cip in list(chat_clients.items()):
                    if cip == target:
                        try:
                            await c.close()
                        except:
                            pass
                await broadcast({'action': 'moderation', 'type': 'ban', 'ip': target})
                continue

            user = data.get('user') or ip
            clean_msg = bleach.clean(
                text,
                tags=['img'],
                attributes={'img': ['src', 'alt']},
                protocols=['http', 'https'],
                strip=True
            )
            entry = {'user': user, 'msg': clean_msg}
            chat_history.append(entry)
            if len(chat_history) > CHAT_HISTORY_LEN:
                chat_history.pop(0)
            await broadcast({'action': 'chat_message', 'entry': entry})

    del chat_clients[ws]
    admin_clients.discard(ws)
    await broadcast({'action': 'viewer_count', 'count': len(chat_clients)})
    return ws

async def broadcast(message):
    for ws in list(chat_clients):
        if not ws.closed:
            try:
                await ws.send_json(message)
            except:
                pass

app = web.Application()
app.add_routes(routes)
app.router.add_static('/', '.')

if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=25570)
