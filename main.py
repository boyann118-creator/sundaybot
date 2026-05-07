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

# 状态机
(MAIN_STATE, BCAST_GROUP, BCAST_MSG, MANAGE_MEMBER_SELECT, MEMBER_ACTION, 
 ADD_GROUP_NAME, KIND_MANAGE_LIST, KIND_ACTION, RENAME_KIND_INPUT) = range(9)
DATA_CACHE = {"groups": ["未分类"], "members": []}

# --- 2. GitHub 核心逻辑 ---
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

# --- 3. 群组登记 (/set_group) ---
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
    if save_to_github(): await update.message.reply_text(msg, parse_mode="Markdown")
    else: await update.message.reply_text("❌ 操作失败，请检查 GitHub")

# --- 4. 私聊控制台 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: return ConversationHandler.END
    sync_from_github()
    kb = [[InlineKeyboardButton("📁 分组/成员管理", callback_data='manage_g')],
          [InlineKeyboardButton("🚀 执行群发", callback_data='start_bc')],
          [InlineKeyboardButton("🔄 刷新数据", callback_data='sync_now')]]
    text = f"🤖 **群发系统控制台**\n当前数据库共有群组: `{len(DATA_CACHE['members'])}` 个"
    reply_markup = InlineKeyboardMarkup(kb)
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else: await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

async def manage_member_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 分页显示群组，防止群组过多导致按钮超限（此处逻辑保持简单）
    kb = [[InlineKeyboardButton(f"📝 {m['remark']} ({m['g_name']})", callback_data=f"edit_{m['chat_id']}")] for m in DATA_CACHE['members']]
    kb.append([InlineKeyboardButton("➕ 添加新分类", callback_data='add_new_kind'), InlineKeyboardButton("🏷️ 分类管理(删/改)", callback_data='kind_manage')])
    kb.append([InlineKeyboardButton("⬅️ 返回主菜单", callback_data='to_start')])
    await update.callback_query.edit_message_text("👥 选择要管理的群组，或管理分类：", reply_markup=InlineKeyboardMarkup(kb))
    return MANAGE_MEMBER_SELECT

# --- 分组(Category)删改逻辑 ---
async def kind_manage_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🛠️ {g}", callback_data=f"kindact_{g}")] for g in DATA_CACHE['groups'] if g != "未分类"]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='manage_g')])
    await update.callback_query.edit_message_text("🏷️ **分类管理**\n选择一个分类进行重命名或删除：", reply_markup=InlineKeyboardMarkup(kb))
    return KIND_MANAGE_LIST

async def kind_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind_name = update.callback_query.data.replace("kindact_", "")
    context.user_data['edit_kind'] = kind_name
    kb = [[InlineKeyboardButton("✏️ 重命名分类", callback_data='kind_rename')],
          [InlineKeyboardButton("🗑️ 删除整个分类", callback_data='kind_delete')],
          [InlineKeyboardButton("⬅️ 返回列表", callback_data='kind_manage')]]
    await update.callback_query.edit_message_text(f"正在管理分类：【{kind_name}】\n(注：删除分类后，原分类下的群组会变为'未分类')", reply_markup=InlineKeyboardMarkup(kb))
    return KIND_ACTION

async def do_kind_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    old_name = context.user_data.get('edit_kind')
    if query.data == "kind_delete":
        DATA_CACHE['groups'].remove(old_name)
        for m in DATA_CACHE['members']:
            if m['g_name'] == old_name: m['g_name'] = "未分类"
        save_to_github()
        await query.answer(f"🗑️ 已删除分类：{old_name}", show_alert=True)
        return await kind_manage_list(update, context)
    elif query.data == "kind_rename":
        await query.edit_message_text(f"✏️ 请输入分类 【{old_name}】 的新名字：")
        return RENAME_KIND_INPUT

async def save_kind_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_name = context.user_data.get('edit_kind')
    new_name = update.message.text.strip()
    if new_name and new_name not in DATA_CACHE['groups']:
        idx = DATA_CACHE['groups'].index(old_name)
        DATA_CACHE['groups'][idx] = new_name
        for m in DATA_CACHE['members']:
            if m['g_name'] == old_name: m['g_name'] = new_name
        save_to_github()
        await update.message.reply_text(f"✅ 分类名已修改为：{new_name}")
    else: await update.message.reply_text("⚠️ 名字无效或已存在")
    return await start(update, context)

# --- 具体群组(Member)管理逻辑 ---
async def member_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = int(query.data.replace("edit_", ""))
    context.user_data['edit_cid'] = cid
    member = next(m for m in DATA_CACHE['members'] if m['chat_id'] == cid)
    
    # 构建分类选择按钮
    kb = [[InlineKeyboardButton(f"🏷 移动至：{g}", callback_data=f"setkind_{g}")] for g in DATA_CACHE['groups']]
    # 新增：从数据库删除该群组的按钮
    kb.append([InlineKeyboardButton("🗑️ 从数据库删除该群组", callback_data="delete_mem")])
    kb.append([InlineKeyboardButton("⬅️ 返回列表", callback_data='manage_g')])
    
    text = f"⚙️ **管理具体群组**\n备注名: {member['remark']}\n当前分组: {member['g_name']}\nID: `{cid}`"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MEMBER_ACTION

async def do_member_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = context.user_data.get('edit_cid')
    member = next(m for m in DATA_CACHE['members'] if m['chat_id'] == cid)
    
    if query.data.startswith("setkind_"):
        new_kind = query.data.replace("setkind_", "")
        member['g_name'] = new_kind
        msg = f"✅ 已将 {member['remark']} 移动至 {new_kind}"
    elif query.data == "delete_mem":
        DATA_CACHE['members'] = [m for m in DATA_CACHE['members'] if m['chat_id'] != cid]
        msg = f"🗑️ 已将 {member['remark']} 从数据库永久删除"
    
    save_to_github()
    await query.answer(msg, show_alert=True)
    return await manage_member_list(update, context)

# --- 添加新分类 ---
async def ask_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("✏️ 请输入新分类的名字：")
    return ADD_GROUP_NAME

async def save_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_kind = update.message.text.strip()
    if new_kind and new_kind not in DATA_CACHE['groups']:
        DATA_CACHE['groups'].append(new_kind)
        save_to_github()
        await update.message.reply_text(f"✅ 新分类 【{new_kind}】 创建成功！")
    return await start(update, context)

# --- 群发逻辑 (交互语符合要求) ---
async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🚀 群发至：{g}", callback_data=f"do_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("🎯 请选择要推送的目标分组：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_target'] = update.callback_query.data.replace("do_", "")
    await update.callback_query.edit_message_text(f"目标组：**{context.user_data['bc_target']}**\n请直接发送要推送的内容（支持文字/图片/文件）：", parse_mode="Markdown")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get('bc_target')
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target]
    msg = await update.message.reply_text(f"📣 正在准备推送，共 {len(ids)} 个目标...")
    count = 0
    for cid in ids:
        try:
            await context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            count += 1
            await asyncio.sleep(0.1)
        except: pass
    # 交互语：成功发送 X/X 至 XXX
    await msg.edit_text(f"✅ 发送任务结束\n成功发送 {count} / {len(ids)} 至 {target}")
    return BCAST_MSG

# --- 5. 运行 ---
app = Flask(''); run_web = lambda: app.run(host='0.0.0.0', port=8080)
@app.route('/')
def home(): return "Sunday Bot Active"

def main():
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    app_tg.add_handler(CommandHandler("set_group", set_group_to_db))
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [CallbackQueryHandler(start, pattern='^sync_now$'), CallbackQueryHandler(manage_member_list, pattern='^manage_g$'), CallbackQueryHandler(bc_select, pattern='^start_bc$')],
            MANAGE_MEMBER_SELECT: [CallbackQueryHandler(member_action_menu, pattern='^edit_'), CallbackQueryHandler(ask_group_name, pattern='^add_new_kind$'), CallbackQueryHandler(kind_manage_list, pattern='^kind_manage$'), CallbackQueryHandler(start, pattern='^to_start$')],
            ADD_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_group_name)],
            KIND_MANAGE_LIST: [CallbackQueryHandler(kind_action_menu, pattern='^kindact_'), CallbackQueryHandler(manage_member_list, pattern='^manage_g$')],
            KIND_ACTION: [CallbackQueryHandler(do_kind_action, pattern='^kind_rename$|^kind_delete$'), CallbackQueryHandler(kind_manage_list, pattern='^kind_manage$')],
            RENAME_KIND_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_kind_rename)],
            MEMBER_ACTION: [CallbackQueryHandler(do_member_action, pattern='^setkind_|^delete_mem$'), CallbackQueryHandler(manage_member_list, pattern='^manage_g$')],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^do_'), CallbackQueryHandler(start, pattern='^to_start$')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do), CallbackQueryHandler(start, pattern='^to_start$')],
        },
        fallbacks=[CommandHandler("start", start)], allow_reentry=True
    )
    app_tg.add_handler(conv); app_tg.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
