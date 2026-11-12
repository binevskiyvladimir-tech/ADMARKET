COMMISSION_RATE = 0.12  # provisional assumption; validate before commercial launch

def _norm(value):
    return (value or '').strip().lower()

def score_item(item, goal, audience, geo, channels):
    hay = ' '.join([str(item.get('name','')), str(item.get('topic','')), str(item.get('channel','')), str(item.get('geo',''))]).lower()
    terms = [w for w in (_norm(goal) + ' ' + _norm(audience)).replace(',', ' ').replace('.', ' ').split() if len(w) >= 4]
    score = sum(3 for term in terms if term in hay)
    item_geo, wanted_geo = _norm(item.get('geo','')), _norm(geo)
    if wanted_geo and (wanted_geo in item_geo or item_geo == 'россия' or wanted_geo == 'россия'):
        score += 3
    if channels and item.get('channel') in channels:
        score += 3
    elif not channels:
        score += 1
    return score

def build_plan(inventory, goal, audience, geo, budget, channels=None):
    channels = channels or []
    budget = max(0, int(budget))
    media_cap = int(budget / (1 + COMMISSION_RATE))
    ranked = []
    for item in inventory:
        score = score_item(item, goal, audience, geo, channels)
        if score > 0 and int(item.get('price', 0)) <= media_cap:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: (pair[0], int(pair[1].get('audience', 0))), reverse=True)
    chosen, remaining, used_channels = [], media_cap, set()
    # First diversify by channel, then use remaining budget.
    for score, item in ranked:
        if len(chosen) >= 4: break
        price = int(item['price'])
        if price <= remaining and item['channel'] not in used_channels:
            chosen.append((score, item)); remaining -= price; used_channels.add(item['channel'])
    for score, item in ranked:
        if len(chosen) >= 4: break
        if any(x[1]['id'] == item['id'] for x in chosen): continue
        price = int(item['price'])
        if price <= remaining:
            chosen.append((score, item)); remaining -= price
    items = [{
        'id': x['id'], 'name': x['name'], 'channel': x['channel'], 'topic': x['topic'],
        'geo': x['geo'], 'audience': x['audience'], 'price': int(x['price']),
        'formats': x['formats'], 'match_score': score, 'demo': bool(x.get('demo', 1)),
        'notes': x.get('notes', '')
    } for score, x in chosen]
    media_total = sum(x['price'] for x in items)
    commission = round(media_total * COMMISSION_RATE)
    total = media_total + commission
    return {
        'items': items, 'budget': budget, 'media_budget_limit': media_cap,
        'media_total': media_total, 'commission_rate': COMMISSION_RATE,
        'commission': commission, 'total': total, 'remaining': max(0, budget-total),
        'demo_only': any(x['demo'] for x in items) if items else True,
        'warning': 'Стартовые записи — демонстрационные. Предложения поставщиков опубликованы после модерации, но их статистика, цена и доступность не подтверждены независимым аудитом.'
    }
