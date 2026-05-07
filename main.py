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

VERSION = "V2.0.1"
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_TOKEN = os.getenv("GH_PAT_TOKEN") 
GH_REPO = "boyann118-creator/sunday-bot-data"
GH_PATH = "groups.json"
RENDER_EXTERNAL_URL = "https://sundaybot.onrender.com"

# 状态机常量
(MAIN_STATE, BCAST_GROUP, BCAST_MSG, MANAGE_MEMBER_SELECT, MEMBER_ACTION, 
 ADD_GROUP_NAME, KIND_MANAGE_LIST, KIND_ACTION, RENAME_KIND_INPUT) = range(9)
DATA_CACHE = {"groups": ["未分類"], "members": []}

# --- 2. GitHub 核心引擎 ---
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
            if "未分類" not in DATA_CACHE["groups"]: DATA_CACHE["groups"].append("未分類")
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
        payload = {"message": f"AutoSync {VERSION} - {time.strftime('%H:%M:%S')}", "content": encoded_content, "sha": remote_sha, "branch": "main"}
        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        return put_resp.status_code in [200, 201]
    except: return False

# --- 3. 环境维持 (心跳) ---
def ping_self():
    while True:
        if RENDER_EXTERNAL_URL:
            try: requests.get(RENDER_EXTERNAL_URL, timeout=10)
            except: pass
        time.sleep(600)

# --- 4. 外部指令 Handler ---
async def set_group_to_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    chat = update.effective_chat
    custom_name = " ".join(context.args) if context.args else chat.title
    sync_from_github()
    existing = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    if not existing:
        DATA_CACHE['members'].append({"chat_id": chat.id, "remark": custom_name, "g_name": "未分類"})
        msg = f"📥 **入庫成功**\n群組: {custom_name}\nID: `{chat.id}`\n默認分組: 未分類"
    else:
        old_name = existing['remark']
        existing['remark'] = custom_name
        msg = f"🔄 **備註已更新**\n原名: {old_name}\n現名: {custom_name}\n分組: {existing['g_name']}"
    if save_to_github(): await update.message.reply_text(msg, parse_mode="Markdown")
    else: await update.message.reply_text("❌ 保存失敗，請檢查 GitHub Token")

# --- 5. 管理控制台逻辑 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    sync_from_github()
    kb = [[InlineKeyboardButton("📁 分組/成員管理", callback_data='manage_g')],
          [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')],
          [InlineKeyboardButton("🔄 刷新數據", callback_data='sync_now')]]
    text = f"🤖 **TG群發助手 {VERSION}**\n當前在冊群組: `{len(DATA_CACHE['members'])}` 個"
    reply_markup = InlineKeyboardMarkup(kb)
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else: await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

async def manage_member_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"📝 {m['remark']} ({m['g_name']})", callback_data=f"edit_{m['chat_id']}")] for m in DATA_CACHE['members']]
    kb.append([InlineKeyboardButton("➕ 添加新分類", callback_data='add_new_kind'), InlineKeyboardButton("🏷️ 分類管理", callback_data='kind_manage')])
    kb.append([InlineKeyboardButton("⬅️ 返回主菜單", callback_data='to_start')])
    await update.callback_query.edit_message_text("👥 選擇群組進行編輯：", reply_markup=InlineKeyboardMarkup(kb))
    return MANAGE_MEMBER_SELECT

async def kind_manage_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🛠️ {g}", callback_data=f"kindact_{g}")] for g in DATA_CACHE['groups'] if g != "未分類"]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='manage_g')])
    await update.callback_query.edit_message_text("🏷️ **分類管理**\n選擇一個分類進行操作：", reply_markup=InlineKeyboardMarkup(kb))
    return KIND_MANAGE_LIST

async def kind_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind_name = update.callback_query.data.replace("kindact_", "")
    context.user_data['edit_kind'] = kind_name
    kb = [[InlineKeyboardButton("✏️ 重命名", callback_data='kind_rename')],
          [InlineKeyboardButton("🗑️ 刪除分類", callback_data='kind_delete')],
          [InlineKeyboardButton("⬅️ 返回", callback_data='kind_manage')]]
    await update.callback_query.edit_message_text(f"正在管理：【{kind_name}】", reply_markup=InlineKeyboardMarkup(kb))
    return KIND_ACTION

async def do_kind_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    old_name = context.user_data.get('edit_kind')
    if query.data == "kind_delete":
        DATA_CACHE['groups'].remove(old_name)
        for m in DATA_CACHE['members']:
            if m['g_name'] == old_name: m['g_name'] = "未分類"
        save_to_github(); await query.answer(f"已刪除分類：{old_name}")
        return await kind_manage_list(update, context)
    elif query.data == "kind_rename":
        await query.edit_message_text(f"✏️ 請輸入 【{old_name}】 的新名字：")
        return RENAME_KIND_INPUT

async def save_kind_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    old_name = context.user_data.get('edit_kind')
    new_name = update.message.text.strip()
    if new_name and new_name not in DATA_CACHE['groups']:
        idx = DATA_CACHE['groups'].index(old_name)
        DATA_CACHE['groups'][idx] = new_name
        for m in DATA_CACHE['members']:
            if m['g_name'] == old_name: m['g_name'] = new_name
        save_to_github(); await update.message.reply_text(f"✅ 已成功更名為：{new_name}")
    return await start(update, context)

async def member_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = int(query.data.replace("edit_", ""))
    context.user_data['edit_cid'] = cid
    member = next(m for m in DATA_CACHE['members'] if m['chat_id'] == cid)
    kb = [[InlineKeyboardButton(f"🏷 移至：{g}", callback_data=f"setkind_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("🗑️ 徹底刪除此群", callback_data="delete_mem")])
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='manage_g')])
    await query.edit_message_text(f"⚙️ **管理：{member['remark']}**\n當前分組：{member['g_name']}", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MEMBER_ACTION

async def do_member_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = context.user_data.get('edit_cid')
    member = next(m for m in DATA_CACHE['members'] if m['chat_id'] == cid)
    if query.data.startswith("setkind_"):
        member['g_name'] = query.data.replace("setkind_", "")
        msg = f"✅ {member['remark']} 已移動至 {member['g_name']}"
    elif query.data == "delete_mem":
        DATA_CACHE['members'] = [m for m in DATA_CACHE['members'] if m['chat_id'] != cid]
        msg = f"🗑️ 已從數據庫刪除 {member['remark']}"
    save_to_github(); await query.answer(msg, show_alert=True)
    return await manage_member_list(update, context)

async def ask_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("✏️ 請輸入新分類名稱："); return ADD_GROUP_NAME

async def save_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_kind = update.message.text.strip()
    if new_kind and new_kind not in DATA_CACHE['groups']:
        DATA_CACHE['groups'].append(new_kind); save_to_github()
        await update.message.reply_text(f"✅ 分類 【{new_kind}】 創建成功！")
    return await start(update, context)

# --- 6. 并发群发 ---
async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🚀 目標組：{g}", callback_data=f"do_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("🎯 請選擇要發送的分組：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_target'] = update.callback_query.data.replace("do_", "")
    await update.callback_query.edit_message_text(f"🚀 目標：**{context.user_data['bc_target']}**\n請直接發送推送內容：", parse_mode="Markdown")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get('bc_target')
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target]
    msg = await update.message.reply_text(f"📣 併發推送中，總數：{len(ids)}...")
    tasks = [context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id) for cid in ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = sum(1 for r in results if not isinstance(r, Exception))
    await msg.edit_text(f"✅ 發送任務結束\n成功發送 {success} / {len(ids)} 至 {target}")
    return BCAST_MSG

# --- 7. 启动服务 ---
app = Flask(''); run_web = lambda: app.run(host='0.0.0.0', port=8080)
@app.route('/')
def home(): return f"TG Send Bot {VERSION} Active"

def main():
    Thread(target=ping_self, daemon=True).start()
    Thread(target=run_web, daemon=True).start()
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
    app_tg.add_handler(conv)
    app_tg.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
