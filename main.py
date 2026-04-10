import os, asyncio, logging, json, requests
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

# GitHub 原始數據鏈接 (Raw URL)
GITHUB_RAW_URL = "https://raw.githubusercontent.com/boyann118-creator/sundaybot/refs/heads/main/groups.json"

# 狀態枚舉
(MAIN_STATE, BCAST_GROUP, BCAST_MSG) = range(3)

# --- 2. 數據同步邏輯 ---
# 內存快取，避免頻繁請求 GitHub
DATA_CACHE = {"groups": [], "members": []}

def sync_from_github():
    """從 GitHub 獲取最新的 JSON 數據"""
    global DATA_CACHE
    try:
        # 使用 requests 獲取數據，設置超時防止卡死
        response = requests.get(GITHUB_RAW_URL, timeout=10)
        if response.status_code == 200:
            DATA_CACHE = response.json()
            logging.info("✅ 已從 GitHub 成功同步最新數據庫")
            return True
        else:
            logging.error(f"❌ 同步失敗，HTTP 狀態碼: {response.status_code}")
            return False
    except Exception as e:
        logging.error(f"❌ 同步出錯: {e}")
        return False

# --- 3. 核心功能函數 ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """主選單"""
    if update.effective_user.id not in ADMIN_IDS: return ConversationHandler.END
    
    # 每次啟動或返回主選單時，自動嘗試刷新數據
    sync_from_github()
    
    context.user_data.clear()
    kb = [
        [InlineKeyboardButton("📁 查看分組名單", callback_data='list_g')],
        [InlineKeyboardButton("🚀 執行群發", callback_data='start_bc')],
        [InlineKeyboardButton("🔄 刷新 GitHub 數據", callback_data='sync_now')]
    ]
    
    text = (
        "🔘 **群發系統控制台 (GitHub 版)**\n\n"
        "目前數據已與 GitHub 同步。如需增刪群組或修改 ID，"
        "請直接編輯 GitHub 上的 `groups.json` 文件。"
    )
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出 GitHub 定義的所有分組"""
    await update.callback_query.answer()
    groups = DATA_CACHE.get("groups", [])
    
    if not groups:
        text = "⚠️ 目前 GitHub 文件中沒有分組數據。"
        kb = [[InlineKeyboardButton("⬅️ 返回", callback_data='to_start')]]
    else:
        text = "🗂️ **分組列表** (點擊查看詳情)："
        kb = [[InlineKeyboardButton(f"📂 {name}", callback_data=f"v_{name}")] for name in groups]
        kb.append([InlineKeyboardButton("⬅️ 返回主選單", callback_data='to_start')])
    
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

async def group_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看分組內成員"""
    query = update.callback_query
    await query.answer()
    g_name = query.data.replace("v_", "")
    
    mems = [m for m in DATA_CACHE.get("members", []) if m['g_name'] == g_name]
    
    text = f"📂 **分組：{g_name}**\n\n成員清單："
    if not mems:
        text += "\n(此分組目前沒有成員)"
    else:
        for m in mems:
            text += f"\n• {m['remark']} (`{m['chat_id']}`)"
    
    kb = [[InlineKeyboardButton("⬅️ 返回列表", callback_data='list_g')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return MAIN_STATE

# --- 群發流程 ---

async def bc_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """選擇目標分組"""
    await update.callback_query.answer()
    groups = DATA_CACHE.get("groups", [])
    if not groups:
        await update.callback_query.edit_message_text("❌ 數據庫為空，無法群發。")
        return MAIN_STATE
        
    kb = [[InlineKeyboardButton(f"🚀 發送至：{name}", callback_data=f"bcg_{name}")] for name in groups]
    kb.append([InlineKeyboardButton("⬅️ 返回", callback_data='to_start')])
    await update.callback_query.edit_message_text("📢 **請選擇群發目標：**", reply_markup=InlineKeyboardMarkup(kb))
    return BCAST_GROUP

async def bc_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """等待用戶輸入內容"""
    g_name = update.callback_query.data.replace("bcg_", "")
    context.user_data['bc_target'] = g_name
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(f"📝 **目標：{g_name}**\n\n請發送群發內容 (文字、圖片、文件均可)：")
    return BCAST_MSG

async def bc_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """執行群發任務"""
    g_name = context.user_data.get('bc_target')
    mems = [m for m in DATA_CACHE.get("members", []) if m['g_name'] == g_name]
    
    success = 0
    status_msg = await update.message.reply_text("⏳ 正在複製並發送消息...")
    
    for m in mems:
        try:
            # 使用 copy_message 原樣轉發用戶的消息
            await context.bot.copy_message(
                chat_id=m['chat_id'], 
                from_chat_id=update.message.chat_id, 
                message_id=update.message.message_id
            )
            success += 1
        except Exception as e:
            logging.error(f"無法發送至 {m['remark']} ({m['chat_id']}): {e}")
            
    await status_msg.edit_text(
        f"✅ **發送任務結束**\n\n📍 目標分組：{g_name}\n🎉 成功數量：{success}/{len(mems)}\n\n"
        "**您可以繼續發送下一條內容，或輸入 /start 返回主選單。**"
    )
    return BCAST_MSG

# --- 4. Web 服務與啟動 ---
app = Flask('')
@app.route('/')
def home(): return "Sunday Bot is running."
def run_web(): app.run(host='0.0.0.0', port=8080)

def main():
    # 啟動時先跑一次同步
    sync_from_github()
    
    Thread(target=run_web).start()
    app_tg = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_STATE: [
                CallbackQueryHandler(list_groups, pattern='^list_g$'),
                CallbackQueryHandler(start, pattern='^to_start$'),
                CallbackQueryHandler(group_detail, pattern='^v_'),
                CallbackQueryHandler(bc_select, pattern='^start_bc$'),
                # 刷新按鈕邏輯
                CallbackQueryHandler(lambda u,c: (u.callback_query.answer("🔄 數據已成功同步" if sync_from_github() else "❌ 同步失敗")) or start(u,c), pattern='^sync_now$'),
            ],
            BCAST_GROUP: [CallbackQueryHandler(bc_get_msg, pattern='^bcg_')],
            BCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, bc_do)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )
    
    app_tg.add_handler(conv)
    app_tg.run_polling()

if __name__ == '__main__': main()
