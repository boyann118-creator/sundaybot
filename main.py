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
(MAIN_STATE, ADD_GNAME, ADD_ID, ADD_REMARK) = range(4)

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

# --- 3. 核心函數：每個動作都有明確的狀態回傳 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """主選單入口"""
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    context.user_data.clear()
    kb = [[InlineKeyboardButton("📁 分組管理", callback_data='list_g')],
          [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')]]
    
    msg_text = "🔘 **群發系統控制台**\n請選擇操作："
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """顯示全部分組"""
    query = update.callback_query
    await query.answer()
    rows = db_op("SELECT name FROM group_names", is_select=True)
    kb = [[InlineKeyboardButton(f"📂 {r['name']}", callback_data=f"v_{r['name']}")] for r in rows]
    kb.append([InlineKeyboardButton("➕ 創建新分組", callback_data='create_g')])
    kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_start')])
    await query.edit_message_text("🗂️ **分組列表**：", reply_markup=InlineKeyboardMarkup(kb))
    return MAIN_STATE

# --- 分組創建流程 ---
async def create_group_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """點擊創建分組按鈕"""
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("✍️ 請發送 **新分組名稱**：")
    return ADD_GNAME

async def create_group_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收並保存分組名稱"""
    name = update.message.text.strip()
    db_op("INSERT OR IGNORE INTO group_names VALUES (?)", (name,))
    await update.message.reply_text(f"✅ 分組「{name}」已創建。")
    # 這裡必須回傳狀態，否則會卡住
    return await start(update, context)

# --- 分組詳情與管理 ---
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

# --- 添加成員流程 ---
async def add_mem_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"👉 **分組：{context.user_data['active_g']}**\n請發送群組 **ID** (需帶 -)：")
    return ADD_ID

async def add_mem_id_rcvd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.message.text.strip()
    if not cid.startswith('-'):
        await update.message.reply_text("❌ 格式錯誤！ID 必須以 - 開頭。請重新輸入：")
        return ADD_ID
    context.user_data['temp_cid'] = cid
    await update.message.reply_text(f"✅ ID 已記錄：`{cid}`\n**現在請發送該群的「備註名稱」：**")
    return ADD_REMARK

async def add_mem_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remark = update.message.text.strip()
    db_op("INSERT OR REPLACE INTO members VALUES (?, ?, ?)", 
          (context.user_data['active_g'], context.user_data['temp_cid'], remark))
    await update.message.reply_text(f"✅ 已錄入：{remark}")
    return await start(update, context)

# --- 刪除與清理 ---
async def remove_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.callback_query.data.replace("rm_", "")
    db_op("DELETE FROM members WHERE chat_id=? AND g_name=?", (cid, context.user_data['active_g']))
    await update.callback_query.answer("已移除")
    return await group_detail(update, context)

async def delete_group_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    g_name = context.user_data.get('active_g')
    db_op("DELETE FROM group_names WHERE name=?", (g_name,))
    db_op("DELETE FROM members WHERE g_name=?", (g_name,))
    await update.callback_query.answer(f"分組已刪除")
    # 刪除後自動跳回列表頁面
    return await list_groups(update, context)

# --- 4. 啟動與 Web 服務 ---
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
                CallbackQueryHandler(create_group_prompt, pattern='^create_g$'),
                CallbackQueryHandler(add_mem_start, pattern='^add_m_flow$'),
                CallbackQueryHandler(delete_group_complete, pattern='^del_g_all$'),
                CallbackQueryHandler(remove_member, pattern='^rm_'),
            ],
            ADD_GNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_save)],
            ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_id_rcvd)],
            ADD_REMARK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mem_save)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
