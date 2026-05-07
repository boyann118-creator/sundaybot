import os, asyncio, logging, json, base64, requests, time
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- 1. 基础配置 ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_TOKEN = os.getenv("GH_PAT_TOKEN") 
GH_REPO = "boyann118-creator/sunday-bot-data"
GH_PATH = "groups.json"

# 状态机：增加了 ADD_GROUP_NAME
(MAIN_STATE, BCAST_GROUP, BCAST_MSG, MANAGE_MEMBER_SELECT, MEMBER_ACTION, ADD_GROUP_NAME) = range(6)
DATA_CACHE = {"groups": ["未分类"], "members": []}

# --- 2. GitHub 核心逻辑 (保持不变) ---
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
            return json_data['sha']
        return None
    except: return None

def save_to_github():
    time.sleep(0.5) 
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        get_resp = requests.get(url, headers=headers, timeout=10)
        if get_resp.status_code != 200: return False
        remote_sha = get_resp.json()['sha']
        json_str = json.dumps(DATA_CACHE, ensure_ascii=False, indent=2)
        encoded_content = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        payload = {"message": f"AutoSync - {time.strftime('%H:%M:%S')}", "content": encoded_content, "sha": remote_sha, "branch": "main"}
        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        return put_resp.status_code in [200, 201]
    except: return False

# --- 3. 增强版群组登记逻辑 ---
async def set_group_to_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    chat = update.effective_chat
    custom_name = " ".join(context.args) if context.args else chat.title
    
    sync_from_github()
    existing = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    
    if not existing:
        DATA_CACHE['members'].append({"chat_id": chat.id, "remark": custom_name, "g_name": "未分类"})
        msg = f"📥 **入库成功**\n群组: {custom_name}\nID: `{chat.id}`\n当前状态: 未分类"
    else:
        old_name = existing['remark']
        existing['remark'] = custom_name
        msg = f"🔄 **群名已更新**\n原名: {old_name}\n现名: {custom_name}\n状态: {existing['g_name']}"

    if save_to_github():
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ 操作失败，请检查 GitHub")

# --- 4. 私聊控制台：新增添加分组功能 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: return ConversationHandler.END
    sync_from_github()
    kb = [
        [InlineKeyboardButton("📁 分组/成员管理", callback_data='manage_g')],
        [InlineKeyboardButton("🚀 执行群发", callback_data='start_bc')],
        [InlineKeyboardButton("🔄 刷新数据", callback_data='sync_now')]
    ]
    text = f"🤖 **群发系统控制台**\n当前数据库共有群组: `{len(DATA_CACHE['members'])}` 个"
    reply_markup = InlineKeyboardMarkup(kb)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

async def manage_member_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"📝 {m['remark']} ({m['g_name']})", callback_data=f"edit_{m['chat_id']}")] for m in DATA_CACHE['members']]
    # 这里加了“添加新分组”按钮
    kb.append([InlineKeyboardButton("➕ 添加新分组", callback_data='add_new_kind')])
    kb.append([InlineKeyboardButton("⬅️ 返回主菜单", callback_data='to_start')])
    await update.callback_query.edit_message_text("👥 选择要管理的群组，或新增分类：", reply_markup=InlineKeyboardMarkup(kb))
    return MANAGE_MEMBER_SELECT

async def ask_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("✏️ 请输入新分组的名字：\n(直接在聊天框发送即可)")
    return ADD_GROUP_NAME

async def save_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_kind = update.message.text.strip()
    if new_kind and new_kind not in DATA_CACHE['groups']:
        DATA_CACHE['groups'].append(new_kind)
        save_to_github()
        await update.message.reply_text(f"✅ 分组 【{new_kind}】 已创建！")
    else:
        await update.message.reply_text(f"⚠️ 分组已存在或名字无效。")
    return await start(update, context) # 回到主菜单

async def member_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = int(query.data.replace("edit_", ""))
    context.user_data['edit_cid'] = cid
    member = next(m for m in DATA_CACHE['members'] if m['chat_id'] == cid)
    kb = [[InlineKeyboardButton(f"🏷 设为分组：{g}", callback_data=f"setkind_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("🗑 从数据库删除", callback_data="delete_mem")])
    kb.append([InlineKeyboardButton("⬅️ 返回列表", callback_data='manage_g')])
    text = f"⚙️ **管理群组**\n名字: {member['remark']}\n当前分组: {member['g_name']}\nID: `{cid}`"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MEMBER_ACTION

async def do_member_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = context.user_data.get('edit_cid')
    member = next(m for m in DATA_CACHE['members'] if m['chat_id'] == cid)
    if query.data.startswith("setkind_"):
        new_kind = query.data.replace("setkind_", "")
        member['g_name'] = new_kind
        msg = f"✅ 已将 {member['remark']} 移动至 【{new_kind}】"
    elif query.data == "delete_mem":
        DATA_CACHE['members'] = [m for m in DATA_CACHE['members'] if m['chat_id'] != cid]
        msg = f"🗑 已将 {member['remark']} 从数据库移除"
    save_to_github()
    await query.answer(msg, show_alert=True)
    return await manage_member_list(update, context)

# --- 群发逻辑 ---
async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🚀 发送至：{g}", callback_data=f"do_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("🎯 请选择目标分组：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_target'] = update.callback_query.data.replace("do_", "")
    await update.callback_query.edit_message_text(f"已选定组：**{context.user_data['bc_target']}**\n请直接发送内容：", parse_mode="Markdown")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get('bc_target')
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target]
    msg = await update.message.reply_text(f"📣 正在推送至 {len(ids)} 个群组...")
    count = 0
    for cid in ids:
        try:
            await context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            count += 1
            await asyncio.sleep(0.1)
        except: pass
    await msg.edit_text(f"✅ 发送任务结束\n成功数: {count} / {len(ids)}")
    return BCAST_MSG

# --- 5. 入口 ---
app = Flask('')
@app.route('/')
def home(): return "Sunday Bot Active"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    app_tg.add_handler(CommandHandler("set_group", set_group_to_db))
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(start, pattern='^sync_now$'),
                CallbackQueryHandler(manage_member_list, pattern='^manage_g$'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
            ],
            MANAGE_MEMBER_SELECT: [
                CallbackQueryHandler(member_action_menu, pattern='^edit_'),
                CallbackQueryHandler(ask_group_name, pattern='^add_new_kind$'), # 进入添加状态
                CallbackQueryHandler(start, pattern='^to_start$'),
            ],
            ADD_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_group_name)], # 接收文字
            MEMBER_ACTION: [
                CallbackQueryHandler(do_member_action, pattern='^setkind_|^delete_mem$'),
                CallbackQueryHandler(manage_member_list, pattern='^manage_g$'),
            ],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^do_'), CallbackQueryHandler(start, pattern='^to_start$')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do), CallbackQueryHandler(start, pattern='^to_start$')],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
