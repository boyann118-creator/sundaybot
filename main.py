import os, asyncio, sqlite3, logging
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# --- 1. 基礎配置 ---
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

# --- 3. 核心功能函數 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """主選單入口"""
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

# --- 分組管理模塊 ---

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    rows = db_op("SELECT name FROM group_names", is_select=True)
    kb = [[InlineKeyboardButton(f"📂 {r['name']}", callback_data=f"v_{r['name']}")] for r in rows]
    kb.append([InlineKeyboardButton("➕ 創建新分組", callback_data='create_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_start')])
    await update.callback_query.edit_message_text("🗂️ **分組列表**：", reply_markup=InlineKeyboardMarkup(kb))
    return MAIN_STATE

async def create_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("✍️ 請發送 **新分組名稱**：")
    return ADD_GNAME

async def create_group_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    db_op("INSERT OR IGNORE INTO group_names VALUES (?)", (name,))
    await update.message.reply_text(f"✅ 分組「{name}」已創建。")
    return await start(update, context)

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
    
    kb.append([InlineKeyboardButton("➕ 添加成員 (錄入 ID)", callback_data='add_m_flow')])
    kb.append([InlineKeyboardButton("🔥 刪除整個分組", callback_data='del_g_all')])
    kb.append([InlineKeyboardButton("⬅️ 返回列表", callback_data='list_g')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 成員錄入流程 ---

async def add_mem_id_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"👉 **分組：{context.user_data['active_g']}**\n請發送群組 **ID** (需帶 -)：")
    return ADD_ID

async def add_mem_id_rcvd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    if not cid.startswith('-'):
        await update.message.reply_text("❌ ID 格式錯誤！必須以 - 開頭，請重新輸入：")
        return ADD_ID
    context.user_data['temp_cid'] = cid
    await update.message.reply_text(f"✅ ID 已記錄：`{cid}`\n**請發送該群的備註名稱：**")
    return ADD_REMARK

async def add_mem_final_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remark = update.message.text.strip()
    db_op("INSERT OR REPLACE INTO members VALUES (?, ?, ?)", 
          (context.user_data['active_g'], context.user_data['temp_cid'], remark))
    await update.message.reply_text(f"✅ 已錄入：{remark}")
    return await start(update, context)

# --- 群發功能 ---

async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    rows = db_op("SELECT name FROM group_names", is_select=True)
    if not rows:
        await update.callback_query.edit_message_text("❌ 暫無分組。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("返回", callback_data='to_start')]]))
        return MAIN_STATE
    kb = [[InlineKeyboardButton(f"🚀 發送至：{r['name']}", callback_data=f"bcg_{r['name']}")] for r in rows]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("📢 **選擇接收消息的分組：**", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = update.callback_query.data.replace("bcg_", "")
    context.user_data['bc_target'] = g_name
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"📝 **目標：{g_name}**\n\n請發送需要群發的內容：")
    return BCAST_MSG

async def bc_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = context.user_data.get('bc_target')
    mems = db_op("SELECT chat_id FROM members WHERE g_name=?", (g_name,), is_select=True)
    count = 0
    for m in mems:
        try:
            await context.bot.copy_message(chat_id=m['chat_id'], from_chat_id=update.message.chat_id, message_id=update.message.message_id)
            count += 1
        except: continue
    await update.message.reply_text(f"✅ 成功發送至 {count} 個群。\n\n**可繼續發送內容，或輸入 /start 返回。**")
    return BCAST_MSG

# --- 啟動與 Web 服務 ---
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
                CallbackQueryHandler(create_group_start, pattern='^create_g$'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
                CallbackQueryHandler(add_mem_id_start, pattern='^add_m_flow$'),
                CallbackQueryHandler(lambda u,c: (db_op("DELETE FROM group_names WHERE name=?", (c.user_data['active_g'],)), db_op("DELETE FROM members WHERE g_name=?", (c.user_data['active_g'],))) or list_groups(u,c), pattern='^del_g_all$'),
                CallbackQueryHandler(lambda u,c: (db_op("DELETE FROM members WHERE chat_id=? AND g_name=?", (u.callback_query.data.replace("rm_",""), c.user_data['active_g']))) or group_detail(u,c), pattern='^rm_'),
            ],
            ADD_GNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_save)],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_id_rcvd)],
            ADD_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_final_save)],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_content, pattern='^bcg_')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_execute)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
