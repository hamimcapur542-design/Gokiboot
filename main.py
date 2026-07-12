import os
import sys
import asyncio
import json
from typing import List
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

# ========== GÜVENLİ API ANAHTARLARI ==========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # Yedekte, şu an kullanılmıyor

# Token kontrolü
if not TELEGRAM_TOKEN:
    print("=" * 50)
    print("❌ HATA: TELEGRAM_BOT_TOKEN tanımlanmamış!")
    print("")
    print("📋 Yapman gereken:")
    print("   export TELEGRAM_BOT_TOKEN='bot_token_buraya'")
    print("")
    print("💡 Veya .env dosyası oluştur:")
    print("   echo 'TELEGRAM_BOT_TOKEN=token' > .env")
    print("=" * 50)
    sys.exit(1)

BASE = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ========== WEBSOCKET ==========
class WSManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, data: dict):
        msg = json.dumps(data, ensure_ascii=False)
        for ws in self.connections[:]:
            try:
                await ws.send_text(msg)
            except:
                self.disconnect(ws)

    @property
    def count(self):
        return len(self.connections)

ws_manager = WSManager()

# ========== WEB ARAMA ==========
async def search_web(query: str, max_results: int = 3) -> List[dict]:
    """DuckDuckGo'da arama yap"""
    url = f"https://html.duckduckgo.com/html/?q={query}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        try:
            res = await client.get(url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")
            
            results = []
            for item in soup.select(".result")[:max_results]:
                title_el = item.select_one(".result__title a")
                snippet_el = item.select_one(".result__snippet")
                
                if title_el:
                    results.append({
                        "title": title_el.get_text(strip=True),
                        "url": title_el.get("href", ""),
                        "snippet": snippet_el.get_text(strip=True) if snippet_el else ""
                    })
            return results
        except Exception as e:
            print(f"Arama hatası: {e}")
            return []

# ========== SAYFA OKU ==========
async def fetch_page_content(url: str) -> str:
    """Web sayfası metnini çıkar"""
    headers = {"User-Agent": "Mozilla/5.0"}
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            res = await client.get(url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(separator=" ", strip=True)[:2000]
        except:
            return ""

# ========== DUCKDUCKGO AI ==========
async def ask_duckduckgo_ai(prompt: str, context: str = "") -> str:
    """DuckDuckGo AI - ücretsiz, limitsiz"""
    
    system_msg = "Sen araştırma asistanısın. Verilen web içeriklerini kullanarak Türkçe, kısa ve net cevaplar ver."
    
    full_prompt = f"{system_msg}\n\n"
    if context:
        full_prompt += f"Web içeriği:\n{context}\n\n"
    full_prompt += f"Kullanıcı sorusu: {prompt}\nCevap:"
    
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": full_prompt}]
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r1 = await client.get("https://duckduckgo.com/duckchat/v1/status", 
                headers={"User-Agent": "Mozilla/5.0", "x-vqd-accept": "1"})
            vqd = r1.headers.get("x-vqd-4")
            
            if not vqd:
                return context[:500] if context else "🤔 Cevap veremiyorum."
            
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "x-vqd-4": vqd
            }
            
            r2 = await client.post("https://duckduckgo.com/duckchat/v1/chat", 
                json=payload, headers=headers)
            
            response = ""
            for line in r2.text.strip().split("\n"):
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        data = json.loads(line[6:])
                        if "message" in data:
                            response += data["message"]
                    except:
                        pass
            
            return response.strip() or "🤔 Bir şey bulamadım."
        except:
            return context[:400] if context else "⚠️ Cevap veremiyorum."

# ========== AJAN MODU ==========
async def agent_mode(query: str) -> str:
    """Ara → Oku → Özetle → Cevapla"""
    
    results = await search_web(query)
    
    if not results:
        return await ask_duckduckgo_ai(query)
    
    first_url = results[0]["url"]
    page_content = await fetch_page_content(first_url)
    
    context = "\n".join([
        f"Kaynak {i+1}: {r['title']}\n{r['snippet']}"
        for i, r in enumerate(results)
    ])
    if page_content:
        context += f"\n\nDetay: {page_content}"
    
    answer = await ask_duckduckgo_ai(query, context)
    sources = "\n".join([f"🔗 {r['title']}: {r['url']}" for r in results[:2]])
    
    return f"{answer}\n\n📚 Kaynaklar:\n{sources}"

# ========== FASTAPI ==========
app = FastAPI(title="Sönmez Ajan Bot")

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

@app.get("/")
async def root():
    return {
        "bot": "Sönmez Ajan Bot",
        "mode": "🕵️ Ajan Modu",
        "ai": "DuckDuckGo AI (GPT-4o-mini)",
        "connections": ws_manager.count
    }

# ========== BOT ==========
async def run_bot():
    print("=" * 50)
    print("🕵️ Sönmez Ajan Bot")
    print("🧠 DuckDuckGo AI (GPT-4o-mini)")
    print("🔍 Web Arama: Açık")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        
        await client.post(f"{BASE}/deleteWebhook", params={"drop_pending_updates": True})
        
        me = await client.get(f"{BASE}/getMe")
        if me.json().get("ok"):
            print(f"✅ Bot: @{me.json()['result']['username']}")
        
        res = await client.get(f"{BASE}/getUpdates")
        updates = res.json().get("result", [])
        offset = updates[-1]["update_id"] + 1 if updates else 0
        
        print("🔄 Bekleniyor...\n")
        
        while True:
            try:
                res = await client.get(f"{BASE}/getUpdates", 
                    params={"offset": offset, "timeout": 30})
                data = res.json()
                
                if not data.get("ok"):
                    continue
                
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    
                    if "message" not in update:
                        continue
                    
                    msg = update["message"]
                    chat_id = msg["chat"]["id"]
                    text = msg.get("text", "")
                    user = msg["from"]["first_name"]
                    
                    print(f"📨 {user}: {text}")
                    
                    if text == "/start":
                        await client.post(f"{BASE}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": f"🕵️ Merhaba {user}!\n\nBen Ajan Bot!\nİnternette araştırır, özetlerim.\n\n💬 Soru sor, araştırayım!\n📢 #hashtag gönderebilirsin."
                        })
                        continue
                    
                    if text == "/stats":
                        await client.post(f"{BASE}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": f"🕵️ Ajan Modu\n👥 İzleyici: {ws_manager.count}\n🧠 AI: GPT-4o-mini"
                        })
                        continue
                    
                    hashtags = [w for w in text.split() if w.startswith("#")]
                    if hashtags:
                        await ws_manager.broadcast({
                            "type": "hashtag", "user": user,
                            "hashtags": hashtags, "text": text
                        })
                    
                    await client.post(f"{BASE}/sendChatAction", json={
                        "chat_id": chat_id, "action": "typing"
                    })
                    
                    status = await client.post(f"{BASE}/sendMessage", json={
                        "chat_id": chat_id, "text": "🔍 Araştırıyorum..."
                    })
                    
                    answer = await agent_mode(text)
                    
                    if status.json().get("ok"):
                        await client.post(f"{BASE}/deleteMessage", json={
                            "chat_id": chat_id,
                            "message_id": status.json()["result"]["message_id"]
                        })
                    
                    await client.post(f"{BASE}/sendMessage", json={
                        "chat_id": chat_id, "text": answer,
                        "disable_web_page_preview": True
                    })
                    
                    print(f"📤 {answer[:60]}...")
                    
            except httpx.ReadTimeout:
                pass
            except Exception as e:
                print(f"⚠️ {e}")
                await asyncio.sleep(2)
            
            await asyncio.sleep(0.3)

# ========== BAŞLAT ==========
if __name__ == "__main__":
    import uvicorn
    import threading
    
    def start_api():
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
    
    threading.Thread(target=start_api, daemon=True).start()
    print("🌐 WebSocket: ws://0.0.0.0:8000/ws\n")
    
    asyncio.run(run_bot())
