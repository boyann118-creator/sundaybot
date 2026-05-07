import os, asyncio, logging, json, base64, requests
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler, ChatMemberHandler
)

# --- 1. 基础配置 ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562, 7738262619]
TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_TOKEN = os.getenv("GH_PAT_TOKEN") 
GH_REPO = "boyann118-creator/sunday-bot-data"  # 注意：这里填你新建的那个数据仓库！
GH_PATH = "groups.json"

(MAIN_STATE, BCAST_GROUP, BCAST_MSG, SET_GROUP_NAME) = range(4)
DATA_CACHE = {"groups": [], "members": []}

# --- 2. GitHub API 核心逻辑 ---

def sync_from_github():
    """从 GitHub API 获取数据 (绕过缓存)"""
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
        logging.error(f"同步失败: {resp.status_code} {resp.text}")
        return None
    except Exception as e:
        logging.error(f"API请求异常: {e}")
        return None

def save_to_github():
    """同步本地缓存到 GitHub"""
    sha = sync_from_github() 
    if not sha: return False
    
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{GH_PATH}"
    headers = {"Authorization": f"token {GH_TOKEN}"}
    encoded_content = base64.b64encode(json.dumps(DATA_CACHE, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')
    
    payload = {
        "message": "Update groups data",
        "content": encoded_content,
        "sha": sha
    }
    
    resp = requests.put(url, headers=headers, json=payload)
    return resp.status_code == 200

# --- 3. 自动化功能 ---

async def on_status_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """需求 1: 自动记录进群/退群"""
    result = update.my_chat_member
    chat = result.chat
    new_status = result.new_chat_member.status
    
    sync_from_github() # 操作前先同步最新
    
    existing = next((m for m in DATA_CACHE['members'] if m['chat_id'] == chat.id), None)
    
    if new_status in ["member", "administrator"]:
        if existing:
            existing['remark'] = chat.title
        else:
            DATA_CACHE['members'].append({"chat_id": chat.id, "remark": chat.title, "g_name": "未分类"})
        save_to_github()
    elif new_status in ["left", "kicked"]:
        DATA_CACHE['members'] = [m for m in DATA_CACHE['members'] if m['chat_id'] != chat.id]
        save_to_github()

# --- 4. 机器人交互逻辑 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    sync_from_github()
    
    kb = [
        [InlineKeyboardButton("📁 分组管理", callback_data='manage_g')],
        [InlineKeyboardButton("🚀 执行群发", callback_data='start_bc')],
        [InlineKeyboardButton("🔄 强制刷新数据", callback_data='sync_now')]
    ]
    text = "🤖 **群发系统 (数据独立版)**\n数据存放在独立仓库，修改不会导致 Render 重启。"
    
    reply_markup = InlineKeyboardMarkup(kb)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return MAIN_STATE

async def manage_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """需求 2: 显示所有群组并支持点击分组"""
    await update.callback_query.answer()
    kb = []
    for m in DATA_CACHE['members']:
        kb.append([InlineKeyboardButton(f"[{m['g_name']}] {m['remark']}", callback_data=f"edit_{m['chat_id']}")])
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    
    await update.callback_query.edit_message_text("🎯 **选择要修改分组的群：**", reply_markup=InlineKeyboardMarkup(kb))
    return MAIN_STATE

async def set_group_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.callback_query.data.split('_')[1]
    context.user_data['editing_chat'] = int(chat_id)
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("📝 请输入该群的新分组名称：\n(直接发送文字，例如：`海外组`)")
    return SET_GROUP_NAME

async def set_group_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text
    chat_id = context.user_data.get('editing_chat')
    
    # 1. 更新成员所属分组
    for m in DATA_CACHE['members']:
        if m['chat_id'] == chat_id:
            m['g_name'] = new_name
            break
            
    # 2. 重新计算所有存在的分组名，确保存儲的分组列表是最簡潔的
    # 这样如果一个分组下没有任何群了，它就会自动从菜单里消失
    current_active_groups = list(set(m['g_name'] for m in DATA_CACHE['members']))
    DATA_CACHE['groups'] = current_active_groups
    
    save_to_github()
    await update.message.reply_text(f"✅ 分组 [{new_name}] 已更新并同步。")
    return await start(update, context)

# --- 群发执行逻辑 ---
async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(f"🚀 发送组：{g}", callback_data=f"do_{g}")] for g in DATA_CACHE['groups']]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("请选择目标分组：", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['bc_target'] = update.callback_query.data.replace("do_", "")
    await update.callback_query.edit_message_text(f"已选定：{context.user_data['bc_target']}\n请发送群发内容：")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_group = context.user_data.get('bc_target')
    ids = [m['chat_id'] for m in DATA_CACHE['members'] if m['g_name'] == target_group]
    
    sent_msg = await update.message.reply_text(f"正在发送至 {len(ids)} 个群...")
    count = 0
    for cid in ids:
        try:
            await context.bot.copy_message(chat_id=cid, from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await sent_msg.edit_text(f"✅ 完成！成功发送: {count}/{len(ids)}")
    return BCAST_MSG

# --- 启动 ---
app = Flask('')
@app.route('/')
def home(): return "Bot Active"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    sync_from_github()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    # 核心监听器：机器人进群/出群
    app_tg.add_handler(ChatMemberHandler(on_status_change, ChatMemberHandler.MY_CHAT_MEMBER))

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(manage_groups, pattern='^manage_g$'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
                CallbackQueryHandler(start, pattern='^to_start$|^sync_now$'),
                CallbackQueryHandler(set_group_prompt, pattern='^edit_'),
            ],
            SET_GROUP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_group_save)],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^do_'), CallbackQueryHandler(start, pattern='^to_start$')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do), CallbackQueryHandler(start, pattern='^to_start$')],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
