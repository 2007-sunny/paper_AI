import os

# ⚠️ 請替換成你目前生成的壞掉 HTML 檔案路徑
html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "J.J.Thomson-1917_Thomson-Parabola", "J.J.Thomson-1917_Thomson-Parabola_reader.html")

if not os.path.exists(html_path):
    print("❌ 找不到 HTML 檔案，請確認路徑是否正確。")
else:
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()

    print("=== 🛠️ 正在自動掃描 HTML 潛在炸彈 ===")

    # 檢查有沒有殘留 Python 註解
    if "# 🌟" in content or "#" in content.split("<script>")[1]:
        print("⚠️ 警告：發現 <script> 區塊中似乎殘留了 Python 的 '#' 註解符號！")

    # 檢查有沒有因為 f-string 沒轉義好，導致產出的網頁直接變成 JavaScript 語法錯誤
    if "display: flex;" in content and "display: {{flex}};" not in content:
        # 如果在生成後的網頁裡看到單個大括號，通常是轉義出錯
        pass

    print("\n【請執行以下動作】: 請幫我把『主控台 (Console) 的紅字』貼上來，我們一分鐘之內就能修好它！")
