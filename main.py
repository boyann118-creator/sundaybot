import os, asyncio, logging, json, base64, requests, time
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- 1. 基础配置与权限检查 ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# 请确保你的 ID 在这个列表里
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_TOKEN = os.getenv("GH_PAT_TOKEN") 
GH_REPO = "boyann118-creator/sunday-bot-data"
GH_PATH = "groups.json"

(MAIN_STATE, BCAST_GROUP, BCAST_MSG, SET_GROUP_NAME) = range(4)
DATA_CACHE = {"groups": ["未分类"], "members": []}

# --- 2. GitHub 核心逻辑 (带详细反馈) ---

def sync_from_github():
    global DATA_CACHE
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            json_data = resp.json()
            content = base64.b64decode(json_data['content']).decode('utf-8')
            DATA_CACHE = json.loads(content)
            print(f"[{time.strftime('%H:%M:%S')}] 🔄 同步成功 | 数据库成员数: {len(DATA_CACHE['members'])}")
            return json_data['sha']
        print(f"[{time.strftime('%H:%M:%S')}] ❌ 同步失败: {resp.status_code} (请检查环境变量 GH_PAT_TOKEN)")
        return None
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 网络异常: {e}")
        return None

def save_to_github():
    # 强制增加延迟，防止瞬时并发冲突
    time.sleep(1) 
    
    # 1. 物理获取最新 SHA（核心逻辑：写入前必须实时拉取一次）
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    try:
        # 获取远程最新的 SHA
        get_resp = requests.get(url, headers=headers, timeout=10)
        if get_resp.status_code != 200:
            print(f"[{time.strftime('%H:%M:%S')}] 🚫 无法同步 SHA: {get_resp.status_code}")
            return False
        
        remote_sha = get_resp.json()['sha']
        
        # 2. 准备 payload
        json_str = json.dumps(DATA_CACHE, ensure_ascii=False, indent=2)
        encoded_content = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        
        payload = {
            "message": f"Sunday Bot AutoSync - {time.strftime('%H:%M:%S')}",
            "content": encoded_content,
            "sha": remote_sha, # 使用刚刚拿到的物理 SHA
            "branch": "main"
        }
        
        # 3. 执行写入
        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        
        if put_resp.status_code in [200, 201]:
            print(f"[{time.strftime('%H:%M:%S')}] ✅ 物理写入成功，新 Commit: {put_resp.json()['commit']['sha'][:7]}")
            return True
        else:
            print(f"[{time.strftime('%H:%M:%S')}] ❌ 写入失败: {put_resp.text}")
            return False
            
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 严重错误: {e}")
        return False

# --- 3. 指令逻辑：入库与分类 ---

async def set_group_to_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/set_group: 仅仅将群组录入数据库"""
    if update.effective_user.id not in ADMIN_IDS: return
    chat = update.effective_chat
    print(f"[{time.strftime('%H:%M:%S')}] 📩 收到入库请求: {chat.title}")

    sync_from_github()
    existing = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    
    if not existing:
        DATA_CACHE['members'].append({"chat_id": chat.id, "remark": chat.title, "g_name": "未分类"})
        if save_to_github():
            await update.message.reply_text(f"📥 **入库成功**\n群组: {chat.title}\nID: `{chat.id}`\n当前状态: 未分类", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ 入库失败，请检查 Render 日志中的权限报错")
    else:
        await update.message.reply_text("ℹ️ 该群组已在数据库中，无需重复录入。")

async def set_group_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/set_kind [名称]: 设置分组"""
    if update.effective_user.id not in ADMIN_IDS: return
    if not context.args:
        await update.message.reply_text("💡 请输入分组名，例如: `/set_kind 海外组`")
        return
        
    new_kind = context.args[0]
    chat = update.effective_chat
    print(f"[{time.strftime('%H:%M:%S')}] 🏷️ 正在为群组 {chat.title} 设置分类: {new_kind}")

    sync_from_github()
    member = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    
    if member:
        member['g_name'] = new_kind
        if new_kind not in DATA_CACHE['groups']:
            DATA_CACHE['groups'].append(new_kind)
        
        if save_to_github():
            await update.message.reply_text(f"✅ 分组已更新\n群组: {chat.title}\n当前分类: 【{new_kind}】")
    else:
        await update.message.reply_text("❌ 请先发送 `/set_group` 将此群加入数据库")

# --- 4. 管理菜单与群发逻辑 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: return ConversationHandler.END
    
    print(f"[{time.strftime('%H:%M:%S')}] 👤 管理员 {uid} 访问主菜单")
    sync_from_github()
    
    kb = [
        [InlineKeyboardButton("📁 分组管理", callback_data='manage_g')],
        [InlineKeyboardButton("🚀 执行群发", callback_data='start_bc')],
        [InlineKeyboardButton("🔄 刷新数据", callback_data='sync_now')]
    ]
    text = f"🤖 **群发系统控制台**\n当前数据库共有群组: `{len(DATA_CACHE['members'])}` 个"
    
    reply_markup = InlineKeyboardMarkup(kb)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

# --- 群发逻辑 (带失败重试日志) ---
async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🚀 发送至：{g}", callback_data=f"do_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("🎯 请选择目标分组：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_target'] = update.callback_query.data.replace("do_", "")
    await update.callback_query.edit_message_text(f"已选定组：**{context.user_data['bc_target']}**\n请直接发送群发内容：", parse_mode="Markdown")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get('bc_target')
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target]
    
    msg = await update.message.reply_text(f"📣 正在向 {len(ids)} 个群组推送消息...")
    count = 0
    for cid in ids:
        try:
            await context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            count += 1
            await asyncio.sleep(0.1) # 频率控制
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] ⚠️ 群发失败 ID {cid}: {e}")

    await msg.edit_text(f"✅ 发送任务结束\n成功数: {count} / {len(ids)}")
    return BCAST_MSG

# --- 5. 运行入口 ---
app = Flask('')
@app.route('/')
def home(): return "Sunday Bot is running."
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    print(f"[{time.strftime('%H:%M:%S')}] 🚀 正在启动 Sunday Bot...")
    Thread(target=run_web).start()
    
    app_tg = Application.builder().token(TOKEN).build()
    
    # 注册群内管理指令
    app_tg.add_handler(CommandHandler("set_group", set_group_to_db))
    app_tg.add_handler(CommandHandler("set_kind", set_group_kind))

    # 私聊菜单逻辑
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(start, pattern='^to_start$|^sync_now$'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
            ],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^do_'), CallbackQueryHandler(start, pattern='^to_start$')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do), CallbackQueryHandler(start, pattern='^to_start$')],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    app_tg.add_handler(conv)
    print(f"[{time.strftime('%H:%M:%S')}] 🤖 监听中，请使用 /set_group 登记群组。")
    app_tg.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
