# The International 2026 → Discord Bot

Автоматичний Discord бот для відстежування матчів The International 2026 зі STRATZ API. Надсилає сповіщення про ігрові дні, матчи, результати та серії напрямки в Discord канал.

## 🚀 Швидкий старт (5 хвилин)

### Крок 1: Створити Discord Webhook

1. Відкрити Discord сервер
2. Перейти в канал, куди надсилати сповіщення
3. **Channel Settings** (шестерня) → **Integrations** → **Webhooks**
4. **New Webhook** → дати ім'я (наприклад "TI 2026 Bot")
5. **Copy Webhook URL** (виглядає так: `https://discordapp.com/api/webhooks/123456789/xxxxx`)

### Крок 2: Отримати STRATZ API Token

1. Перейти на https://stratz.com/settings/api
2. Скопіювати Bearer Token (виглядає так: `api_xxxxxxxxxxxx`)

### Крок 3: Додати GitHub Secrets

1. Перейти в репозиторій
2. **Settings** → **Secrets and variables** → **Actions**
3. **New repository secret** (додати обидва):
   - **Name:** `STRATZ_API_TOKEN` | **Value:** `api_xxxxxxxxxxxx`
   - **Name:** `DISCORD_WEBHOOK_URL` | **Value:** `https://discordapp.com/api/webhooks/...`

### Крок 4: Додати GitHub Variable

1. У тому ж місці **Secrets and variables** → **Actions** → вкладка **Variables**
2. **New repository variable**:
   - **Name:** `STRATZ_LEAGUE_ID` | **Value:** (ID ліги зі STRATZ — чекаємо поки появиться)

> Якщо ID ліги ще невідомий, пропустити цей крок — можна додати пізніше.

### Крок 5: Запустити тестовий запуск

1. **Actions** → **The International 2026 Discord updates** 
2. **Run workflow** → **Run workflow**
3. Дочекатися виконання (зелена галочка)
4. Перевірити Discord канал 📢

---

## 📋 Що буде надіслано в Discord

### 1️⃣ Оголошення ігрового дня (один раз на день)
```
📅 **ДЕНЬ 1 THE INTERNATIONAL 2026**
https://www.youtube.com/@Dota2_maincast
```

### 2️⃣ Матч розпочався
```
🔴 **МАТЧ РОЗПОЧАВСЯ**
🇪🇺 Team Liquid 🆚 🇨🇳 Xtreme Gaming
Гра 1
```

### 3️⃣ Гра закінчилася
```
🎮 **ГРА 1 ЗАВЕРШИЛАСЯ**
🇪🇺 Team Liquid 1 — 0 🇨🇳 Xtreme Gaming
⏱ Тривалість: 42:15
```

### 4️⃣ Матч завершився
```
🏆 **МАТЧ ЗАВЕРШИВСЯ**
🇪🇺 Team Liquid 3 — 1 🇨🇳 Xtreme Gaming
🥇 Переможець: 🇪🇺 Team Liquid
```

Усі сповіщення включають **Embed** з логотипами обох команд та їх регіонами.

---

## ⚙️ Налаштування

### GitHub Variables (опціонально)
- `SERIES_BEST_OF` — формат серії: `2`, `3` або `5` (за замовчуванням: `3`)
- `LIQUIPEDIA_USER_AGENT` — User-Agent для Liquipedia API (за замовчуванням: `TI2026DiscordBot/1.0`)

### Локальне тестування

```powershell
# Windows PowerShell
$env:STRATZ_API_TOKEN = "твій-токен"
$env:STRATZ_LEAGUE_ID = "ID-ліги"
$env:DISCORD_WEBHOOK_URL = "твій-вебхук"
python main.py
```

```bash
# Linux/Mac
export STRATZ_API_TOKEN="твій-токен"
export STRATZ_LEAGUE_ID="ID-ліги"
export DISCORD_WEBHOOK_URL="твій-вебхук"
python main.py
```

---

## 📅 Розклад виконання

- **Період:** 13-23 серпня 2026 (турнір у Шанхаї)
- **Час:** 01:00-16:00 UTC (09:00-00:00 Shanghai)
- **Частота:** Кожні 5 хвилин
- **Поза турніром:** Спить (економія ресурсів GitHub Actions)

---

## 🎯 Команди турніру (16 учасників)

| Прапор | Команда | Регіон |
|--------|---------|--------|
| 🇷🇺 | Team Spirit | CIS |
| 🇨🇳 | Xtreme Gaming | Китай |
| 🇨🇳 | LGD Gaming | Китай |
| 🇨🇳 | PSG.LGD | Китай |
| 🇪🇺 | Team Liquid | Європа |
| 🇪🇺 | Gaimin Gladiators | Європа |
| 🇦🇹 | Tundra Esports | Європа |
| 🇪🇺 | OG | Європа |
| 🇺🇸 | Evil Geniuses | Північна Америка |
| 🇺🇸 | Nouns | Північна Америка |
| 🇨🇦 | Shopify Rebellion | Північна Америка |
| 🇵🇦 | beastcoast | Південна Америка |
| 🇵🇪 | Thunder Awaken | Південна Америка |
| 🇲🇾 | Fnatic | Південно-Східна Азія |
| 🇵🇭 | Talon Esports | Південно-Східна Азія |
| 🇸🇦 | Team Falcons | Близький Схід |

---

## 🔍 Логування

GitHub Actions логи можна переглядати:
1. **Actions** → **The International 2026 Discord updates**
2. Вибрати запуск
3. **Publish a new match update** — див. логи

---

## ❓ FAQ

**Q: Помилка "Missing required environment variable"**
A: Перевірити, що усі secrets і variables додані правильно в GitHub Settings.

**Q: Матчі не відображаються**
A: STRATZ розпізнає ліги після турніру. Чекати оновлення на https://stratz.com/leagues.

**Q: Як змінити канал Discord?**
A: Нова webhook для іншого каналу → оновити `DISCORD_WEBHOOK_URL` secret.

**Q: Як вимкнути бота?**
A: Видалити secrets або приховати repository.

---

## 📞 Потрібна допомога?

- STRATZ API: https://stratz.com/api
- Discord Webhooks: https://discord.com/developers/docs/resources/webhook
- GitHub Actions: https://docs.github.com/en/actions

---

**Версія:** 1.0  
**Останнє оновлення:** 11 серпня 2026
