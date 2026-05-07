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

VERSION = "V2.0"
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_TOKEN = os.getenv("GH_PAT_TOKEN") 
GH_REPO = "boyann118-creator/sunday-bot-data"
GH_PATH = "groups.json"
# 自动检测 Render 分配的 URL 用于预防休眠
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "") 

# 状态机
(MAIN_STATE, BCAST_GROUP, BCAST_MSG, MANAGE_MEMBER_SELECT, MEMBER_ACTION, 
 ADD_GROUP_NAME, KIND_MANAGE_LIST, KIND_ACTION, RENAME_KIND_INPUT) = range(9)
DATA_CACHE = {"groups": ["未分类"], "members": []}

# --- 2. 核心逻辑 (GitHub 同步) ---
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
        payload = {"message": f"AutoSync V2.0 - {time.strftime('%H:%M:%S')}", "content": encoded_content, "sha": remote_sha, "branch": "main"}
        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        return put_resp.status_code in [200, 201]
    except: return False

# --- 3. 预防休眠机制 (Anti-Sleep) ---
def ping_self():
    """每10分钟访问一次自身，保持 Render 在线"""
    while True:
        if RENDER_EXTERNAL_URL:
            try:
                requests.get(RENDER_EXTERNAL_URL, timeout=10)
                logging.info(f"💓 自我唤醒心跳已发送: {RENDER_EXTERNAL_URL}")
            except Exception as e:
                logging.error(f"💔 唤醒失败: {e}")
        time.sleep(600) # 10分钟一次

# --- 4. 私聊控制台交互 ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: return ConversationHandler.END
    sync_from_github()
    kb = [[InlineKeyboardButton("📁 分组/成员管理", callback_data='manage_g')],
          [InlineKeyboardButton("🚀 执行群发", callback_data='start_bc')],
          [InlineKeyboardButton("🔄 刷新数据", callback_data='sync_now')]]
    text = f"🤖 **TG群发助手 {VERSION}**\n当前数据库群组: `{len(DATA_CACHE['members'])}` 个"
    reply_markup = InlineKeyboardMarkup(kb)
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else: await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

# (此处省略中间重复的管理功能函数 manage_member_list, kind_manage_list 等，保持逻辑与刚才一致)
# ... 为了保证代码完整性，以下是整合后的核心交互逻辑 ...

async def manage_member_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"📝 {m['remark']} ({m['g_name']})", callback_data=f"edit_{m['chat_id']}")] for m in DATA_CACHE['members']]
    kb.append([InlineKeyboardButton("➕ 添加新分类", callback_data='add_new_kind'), InlineKeyboardButton("🏷️ 分类管理(删/改)", callback_data='kind_manage')])
    kb.append([InlineKeyboardButton("⬅️ 返回主菜单", callback_data='to_start')])
    await update.callback_query.edit_message_text("👥 选择群组或管理分类：", reply_markup=InlineKeyboardMarkup(kb))
    return MANAGE_MEMBER_SELECT

async def kind_manage_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🛠️ {g}", callback_data=f"kindact_{g}")] for g in DATA_CACHE['groups'] if g != "未分类"]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='manage_g')])
    await update.callback_query.edit_message_text("🏷️ **分类管理**：", reply_markup=InlineKeyboardMarkup(kb))
    return KIND_MANAGE_LIST

async def member_action_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    cid = int(query.data.replace("edit_", ""))
    context.user_data['edit_cid'] = cid
    member = next(m for m in DATA_CACHE['members'] if m['chat_id'] == cid)
    kb = [[InlineKeyboardButton(f"🏷 移动至：{g}", callback_data=f"setkind_{g}")] for g in DATA_CACHE['groups']]
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
        new_kind = query.data.replace("setkind_", ""); member['g_name'] = new_kind
        msg = f"✅ 已将 {member['remark']} 移动至 {new_kind}"
    elif query.data == "delete_mem":
        DATA_CACHE['members'] = [m for m in DATA_CACHE['members'] if m['chat_id'] != cid]
        msg = f"🗑️ 已从数据库删除 {member['remark']}"
    save_to_github(); await query.answer(msg, show_alert=True)
    return await manage_member_list(update, context)

async def ask_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.edit_message_text("✏️ 请输入新分类的名字："); return ADD_GROUP_NAME

async def save_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_kind = update.message.text.strip()
    if new_kind and new_kind not in DATA_CACHE['groups']:
        DATA_CACHE['groups'].append(new_kind); save_to_github()
        await update.message.reply_text(f"✅ 分类 【{new_kind}】 创建成功！")
    return await start(update, context)

# --- 5. 并发群发逻辑 (核心优化) ---
async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🚀 发送至：{g}", callback_data=f"do_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("🎯 请选择目标分组：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_target'] = update.callback_query.data.replace("do_", "")
    await update.callback_query.edit_message_text(f"目标组：**{context.user_data['bc_target']}**\n请发送要群发的内容：", parse_mode="Markdown")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get('bc_target')
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target]
    msg = await update.message.reply_text(f"📣 正在并行推送至 {len(ids)} 个目标...")
    
    # 构建并发任务
    tasks = [
        context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id) 
        for cid in ids
    ]
    # 并发执行所有任务，return_exceptions=True 防止单个群失败导致全部中断
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success = sum(1 for r in results if not isinstance(r, Exception))
    
    await msg.edit_text(f"✅ 发送任务结束\n成功发送 {success} / {len(ids)} 至 {target}")
    return BCAST_MSG

# --- 6. 运行入口 ---
app = Flask(''); run_web = lambda: app.run(host='0.0.0.0', port=8080)
@app.route('/')
def home(): return f"TG群发助手 {VERSION} 运行中"

def main():
    # 启动预防休眠心跳线程
    Thread(target=ping_self, daemon=True).start()
    # 启动 Web 服务线程
    Thread(target=run_web, daemon=True).start()
    
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [CallbackQueryHandler(start, pattern='^sync_now$'), CallbackQueryHandler(manage_member_list, pattern='^manage_g$'), CallbackQueryHandler(bc_select, pattern='^start_bc$')],
            MANAGE_MEMBER_SELECT: [CallbackQueryHandler(member_action_menu, pattern='^edit_'), CallbackQueryHandler(ask_group_name, pattern='^add_new_kind$'), CallbackQueryHandler(kind_manage_list, pattern='^kind_manage$'), CallbackQueryHandler(start, pattern='^to_start$')],
            ADD_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_group_name)],
            MEMBER_ACTION: [CallbackQueryHandler(do_member_action, pattern='^setkind_|^delete_mem$'), CallbackQueryHandler(manage_member_list, pattern='^manage_g$')],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^do_'), CallbackQueryHandler(start, pattern='^to_start$')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do), CallbackQueryHandler(start, pattern='^to_start$')],
        },
        fallbacks=[CommandHandler("start", start)], allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling(drop_pending_updates=True)

if __name__ == '__main__': main()
