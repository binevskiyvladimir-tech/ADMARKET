import json, os, urllib.request, urllib.error

def ask_ai(message, history, brief, shortlist):
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    model = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
    base_url = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1').rstrip('/')
    if not api_key:
        return fallback_reply(brief, shortlist)
    system = (
        'Ты — AI-медиабайер ADMARKET. Отвечай на русском, естественно и конкретно. '
        'Собирай бриф: бизнес/продукт, цель, аудитория, география, бюджет, каналы. '
        'Задавай не более одного уточняющего вопроса за раз и не повторяй уже известное. '
        'Когда информации достаточно, объясни предварительную стратегию. Используй ТОЛЬКО shortlist. '
        'Не выдумывай площадки, цены, охваты, контакты, гарантии или доступность. '
        'Все записи текущего каталога демонстрационные, цены тестовые. Комиссия 12% — гипотеза MVP, не утверждённый тариф. '
        'Не обещай оплату, бронирование или запуск: заявка требует ручной проверки. '
        'Законность площадок, маркировка и обязательные платежи требуют отдельной юридической проверки.'
    )
    context = 'Бриф клиента:\n' + json.dumps(brief, ensure_ascii=False) + '\nКандидаты каталога:\n' + json.dumps(shortlist, ensure_ascii=False)
    messages = [{'role':'system','content':system+'\n\n'+context}]
    for item in history[-12:]:
        if item.get('role') in ('user','assistant') and isinstance(item.get('content'), str):
            messages.append({'role':item['role'], 'content':item['content'][:3000]})
    messages.append({'role':'user','content':message[:3000]})
    payload = {'model':model,'temperature':0.35,'messages':messages}
    req = urllib.request.Request(base_url+'/chat/completions', data=json.dumps(payload).encode(), headers={'Authorization':'Bearer '+api_key,'Content-Type':'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            data = json.loads(response.read().decode())
        return data['choices'][0]['message']['content'].strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='ignore')[:350]
        return f'AI API вернул HTTP {exc.code}. Проверь API-ключ, модель и лимиты. {detail}'
    except Exception as exc:
        return f'AI API временно недоступен: {exc}. Можешь собрать медиаплан через форму ниже.'

def fallback_reply(brief, shortlist):
    missing = [label for key,label in [('business','продукт или бизнес'),('goal','цель рекламы'),('audience','целевую аудиторию'),('geo','географию'),('budget','бюджет')] if not brief.get(key)]
    if missing:
        return 'Сейчас включён резервный режим без языковой модели. Чтобы собрать бриф, уточни: '+', '.join(missing[:2])+'. Или заполни форму медиаплана ниже.'
    if not shortlist:
        return 'По текущему демонстрационному каталогу не нашлось подходящих вариантов. Попробуй расширить географию, бюджет или каналы.'
    lines = ['По твоему брифу предварительно подходят:']
    for x in shortlist[:4]:
        lines.append(f"• {x['name']} — {x['channel']}, тестовая цена {x['price']:,} ₽".replace(',', ' '))
    lines.append('Это демонстрационные записи, цены и наличие мест не подтверждены. Для полноценного диалога подключи API-ключ на сервере.')
    return '\n'.join(lines)
