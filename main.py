import os, asyncio, sqlite3, logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- 1. 配置 ---
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
ADMIN_IDS = [5558898787, 7549117882, 6914258528, 7309768391, 7156620562]
TOKEN = os.getenv("TELEGRAM_TOKEN")

# 狀態枚舉
(MAIN_STATE, ADD_GNAME, ADD_ID, ADD_REMARK, BCAST_GROUP, BCAST_MSG) = range(6)

# --- 2. 數據庫 ---
def db_op(sql, params=(), is_select=False):
    with sqlite3.connect('bot_data.db') as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, params)
        if is_select: return cur.fetchall()
        conn.commit()

def init_db():
    db_op('CREATE TABLE IF NOT EXISTS group_names (name TEXT PRIMARY KEY)')
    db_op('CREATE TABLE IF NOT EXISTS members (g_name TEXT, chat_id TEXT, remark TEXT, UNIQUE(g_name, chat_id))')

# --- 3. 核心功能 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    context.user_data.clear()
    kb = [[InlineKeyboardButton("📁 分組管理", callback_data='list_g')],
          [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')]]
    text = "🔘 **群發系統控制台**\n請選擇操作："
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 分組管理邏輯 (保持穩定) ---
async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    rows = db_op("SELECT name FROM group_names", is_select=True)
    kb = [[InlineKeyboardButton(f"📂 {r['name']}", callback_data=f"v_{r['name']}")] for r in rows]
    kb.append([InlineKeyboardButton("➕ 創建新分組", callback_data='create_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_start')])
    await update.callback_query.edit_message_text("🗂️ **分組列表**：", reply_markup=InlineKeyboardMarkup(kb))
    return MAIN_STATE

async def group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    g_name = query.data.replace("v_", "") if "v_" in query.data else context.user_data.get('active_g')
    context.user_data['active_g'] = g_name
    mems = db_op("SELECT chat_id, remark FROM members WHERE g_name=?", (g_name,), is_select=True)
    text = f"📂 **分組：{g_name}**\n\n成員清單："
    kb = []
    for m in mems:
        text += f"\n• {m['remark']} (`{m['chat_id']}`)"
        kb.append([InlineKeyboardButton(f"🗑️ 移除 {m['remark']}", callback_data=f"rm_{m['chat_id']}")])
    kb.append([InlineKeyboardButton("➕ 添加群組 (錄入 ID)", callback_data='add_m_flow')])
    kb.append([InlineKeyboardButton("🔥 刪除整個分組", callback_data='del_g_all')])
    kb.append([InlineKeyboardButton("⬅️ 返回列表", callback_data='list_g')])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 群發核心邏輯 ---

async def broadcast_select_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """點擊『執行群發』後，讓你選發給哪個組"""
    await update.callback_query.answer()
    rows = db_op("SELECT name FROM group_names", is_select=True)
    if not rows:
        await update.callback_query.edit_message_text("❌ 目前沒有任何分組，請先創建分組。", 
                                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回", callback_data='to_start')]]))
        return MAIN_STATE
    
    kb = [[InlineKeyboardButton(f"🚀 發送至：{r['name']}", callback_data=f"bcg_{r['name']}")] for r in rows]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("📢 **選擇接收消息的分組：**", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def broadcast_content_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """選擇分組後，提示輸入內容"""
    g_name = update.callback_query.data.replace("bcg_", "")
    context.user_data['bc_target'] = g_name
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"📝 **目標分組：{g_name}**\n\n**請發送需要群發的內容：**\n(支持文字、圖片、表情符號)")
    return BCAST_MSG

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """執行真正的群發動作"""
    g_name = context.user_data.get('bc_target')
    content = update.message # 獲取整條消息對象
    mems = db_op("SELECT chat_id, remark FROM members WHERE g_name=?", (g_name,), is_select=True)
    
    if not mems:
        await update.message.reply_text(f"❌ 分組「{g_name}」內沒有成員，無法發送。")
        return await start(update, context)

    success_count = 0
    fail_names = []

    msg_status = await update.message.reply_text(f"⏳ 正在向「{g_name}」群發中 (0/{len(mems)})...")

    for m in mems:
        try:
            # 使用 copy_message 可以完美複製文字、圖片、視頻等
            await context.bot.copy_message(chat_id=m['chat_id'], from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            success_count += 1
        except Exception as e:
            fail_names.append(m['remark'])
            logging.error(f"發送失敗 {m['remark']}: {e}")
    
    result_text = f"✅ **發送任務完成！**\n\n📍 目標分組：{g_name}\n🎉 成功發送：{success_count} 個群\n"
    if fail_names:
        result_text += f"⚠️ 失敗：{', '.join(fail_names)}\n(請檢查機器人是否在群內或被封鎖)"
    
    result_text += "\n\n🔄 **您可以繼續發送內容進行群發，或輸入 /start 返回主選單。**"
    await msg_status.edit_text(result_text, parse_mode="Markdown")
    
    # 保持在 BCAST_MSG 狀態，實現「繼續發送」功能
    return BCAST_MSG

# --- 4. 啟動入口 ---
app = Flask('')
@app.route('/')
def home(): return "Bot Online"
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    init_db()
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(list_groups, pattern='^list_g$'),
                CallbackQueryHandler(start, pattern='^to_start$'),
                CallbackQueryHandler(group_detail, pattern='^v_'),
                CallbackQueryHandler(lambda u,c: u.callback_query.edit_message_text("✍️ 請發送新分組名稱：") or ADD_GNAME, pattern='^create_g$'),
                CallbackQueryHandler(lambda u,c: u.callback_query.edit_message_text(f"👉 **分組：{c.user_data['active_g']}**\n請發送群 ID：") or ADD_ID, pattern='^add_m_flow$'),
                CallbackQueryHandler(broadcast_select_group, pattern='^start_bc$'),
                CallbackQueryHandler(lambda u,c: (db_op("DELETE FROM group_names WHERE name=?", (c.user_data['active_g'],)), db_op("DELETE FROM members WHERE g_name=?", (c.user_data['active_g'],))) or list_groups(u,c), pattern='^del_g_all$'),
                CallbackQueryHandler(lambda u,c: (db_op("DELETE FROM members WHERE chat_id=? AND g_name=?", (u.callback_query.data.replace("rm_",""), c.user_data['active_g']))) or group_detail(u,c), pattern='^rm_'),
            ],
            ADD_GNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: db_op("INSERT OR IGNORE INTO group_names VALUES (?)", (u.message.text.strip(),)) or start(u,c))],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: (c.user_data.update({'t_cid': u.message.text.strip()})) or u.message.reply_text("✅ ID 已記錄，請發送備註：") or ADD_REMARK)],
            ADD_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: db_op("INSERT OR REPLACE INTO members VALUES (?, ?, ?)", (c.user_data['active_g'], c.user_data['t_cid'], u.message.text.strip())) or start(u,c))],
            BCAST_GROUP: [CallbackQueryHandler(broadcast_content_prompt, pattern='^bcg_')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, do_broadcast)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
