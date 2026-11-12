"""Explainable, conservative audience-quality triage. Not an external verification service."""
import hashlib


def assess_account(con, account_id: int):
    account = con.execute("SELECT * FROM platform_accounts WHERE id=?", (account_id,)).fetchone()
    if not account:
        return None
    snapshots = con.execute("SELECT * FROM platform_metric_snapshots WHERE account_id=? ORDER BY sampled_at DESC,id DESC LIMIT 2", (account_id,)).fetchall()
    if not snapshots:
        return None
    cur = snapshots[0]
    prev = snapshots[1] if len(snapshots) > 1 else None
    flags=[]
    growth=0.0
    previous_followers=int(prev['followers']) if prev else 0
    if prev and int(prev['followers']) > 0:
        growth=(int(cur['followers'])-int(prev['followers']))/int(prev['followers'])*100
        if growth > 50:
            flags.append(f"Рост подписчиков {growth:.1f}% между двумя последними снимками — проверить источник и интервал")
    ratio=(float(cur['avg_views'])/int(cur['followers'])) if int(cur['followers']) else (float('inf') if int(cur['avg_views']) else 0.0)
    if int(cur['followers']) == 0 and int(cur['avg_views']) > 0:
        flags.append("Есть просмотры при нулевом числе подписчиков")
    elif ratio > 3:
        flags.append(f"Средние просмотры превышают число подписчиков в {ratio:.2f} раза")
    er=float(cur['engagement_rate'])
    if er > 30:
        flags.append(f"Необычно высокий ER {er:.2f}% — проверить методику расчёта")
    if int(cur['avg_reach']) > max(int(cur['followers'])*5, 100000):
        flags.append("Охват существенно превышает базу подписчиков")
    score=100
    for flag in flags:
        if flag.startswith('Рост'): score-=25
        elif flag.startswith('Есть просмотры'): score-=30
        elif flag.startswith('Средние просмотры'): score-=20
        elif flag.startswith('Необычно'): score-=15
        else: score-=15
    score=max(0,min(100,score))
    confidence='insufficient' if not prev else ('low' if len(flags)>=2 else 'medium' if flags else 'high')
    cur_id=int(cur['id'])
    con.execute("INSERT INTO platform_trust_assessments(account_id,trust_score,confidence,flags,previous_followers,current_followers,growth_pct,view_to_follower_ratio) VALUES(?,?,?,?,?,?,?,?)",
        (account_id,score,confidence,'; '.join(flags),previous_followers,int(cur['followers']),round(growth,2),round(ratio,4) if ratio != float('inf') else 999999.0))
    for flag in flags:
        if flag.startswith('Рост'): typ='follower_growth'; severity='high' if growth>100 else 'medium'
        elif flag.startswith('Есть просмотры'): typ='views_without_followers'; severity='high'
        elif flag.startswith('Средние просмотры'): typ='views_ratio_outlier'; severity='medium'
        elif flag.startswith('Необычно'): typ='engagement_outlier'; severity='low'
        else: typ='reach_outlier'; severity='medium'
        key=hashlib.sha256(f"{account_id}:{cur_id}:{typ}".encode()).hexdigest()
        con.execute("INSERT OR IGNORE INTO platform_trust_signals(signal_key,account_id,snapshot_id,signal_type,severity,details) VALUES(?,?,?,?,?,?)",
            (key,account_id,cur_id,typ,severity,flag))
    return {"account_id":account_id,"trust_score":score,"confidence":confidence,"flags":flags,
            "previous_followers":previous_followers,"current_followers":int(cur['followers']),"growth_pct":round(growth,2),
            "view_to_follower_ratio":round(ratio,4) if ratio != float('inf') else None,"snapshot_id":cur_id}
