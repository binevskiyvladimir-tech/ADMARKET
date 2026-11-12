import tempfile,re,os,json,hmac,hashlib
from pathlib import Path
from fastapi.testclient import TestClient
from app import db
import app.main as main
from app.main import app
from app.auth import hash_password

tmp=tempfile.TemporaryDirectory(); db.DATA=Path(tmp.name); db.DB_PATH=db.DATA/'test.db'
os.environ['PAYMENT_MODE']='test'
os.environ['SANDBOX_WEBHOOK_SECRET']='test-webhook-secret-at-least-24-chars'
sent=[]; main.send_account_email=lambda to,subject,link: (sent.append((to,subject,link)) or True)
with TestClient(app) as c:
    assert c.get('/health').json()['version']=='2.8.0'
    html=c.get('/register').text
    csrf=re.search(r'name="csrf" value="([^"]+)',html).group(1)
    r=c.post('/register',data={'csrf':csrf,'email':'buyer@example.test','password':'StrongPassword123','role':'advertiser','name':'Buyer','company':'Buyer Co'},follow_redirects=False)
    assert r.status_code==303, r.text
    verify_link=sent[-1][2]
    verify_token=verify_link.split('token=')[1]
    assert c.get('/verify-email',params={'token':verify_token}).status_code==200
    with db.connect() as con:
        uid=con.execute("SELECT id FROM users WHERE email='buyer@example.test'").fetchone()['id']
        supplier=con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified,supplier_verification) VALUES(?,?,?,?,?,1,'verified')",('s@example.test',hash_password('StrongPassword123'),'supplier','Supplier','Supplier Co')).lastrowid
        req=con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(?,?,?,?,?)",(uid,'Campaign','Leads','RU',10000)).lastrowid
        resp=con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(?,?,?,'accepted')",(req,supplier,1000)).lastrowid
        placement=con.execute("INSERT INTO placements(response_id,status) VALUES(?,'completed')",(resp,)).lastrowid
        terms=con.execute("INSERT INTO placement_terms(placement_id,version,deliverables,price,proposed_by,status) VALUES(?,1,'Placement',1000,?,'accepted')",(placement,supplier)).lastrowid
        con.execute("INSERT INTO financial_estimates(placement_id,terms_id,advertiser_id,supplier_id,base_amount,commission_rate,commission_amount,supplier_estimated_net,advertiser_estimated_total) VALUES(?,?,?,?,?,?,?,?,?)",(placement,terms,uid,supplier,1000,12,120,880,1000))
        con.commit()
    loginhtml=c.get('/login').text; csrf=re.search(r'name="csrf" value="([^"]+)',loginhtml).group(1)
    r=c.post('/login',data={'csrf':csrf,'email':'buyer@example.test','password':'StrongPassword123'},follow_redirects=False)
    assert r.status_code==303, r.text
    buyer_workspace=c.get('/dashboard').text
    assert 'Управление рекламой' in buyer_workspace and 'workspace-overview' in buyer_workspace
    assert 'id="campaigns"' in buyer_workspace and 'Новая кампания' in buyer_workspace
    buyer_csrf=re.search(r'name="csrf" value="([^"]+)',buyer_workspace).group(1)
    created=c.post('/advertiser/campaigns',data={'csrf':buyer_csrf,'title':'Spring Launch','objective':'Acquire qualified leads','budget':'25000','geo':'Russia','channels':'VK, Telegram','audience':'SMB founders','start_date':'2026-12-01','end_date':'2026-12-31','request_id':str(req)},follow_redirects=False)
    assert created.status_code==303 and '#campaigns' in created.headers['location']
    with db.connect() as con:
        campaign=con.execute("SELECT * FROM campaigns WHERE advertiser_id=? AND title='Spring Launch'",(uid,)).fetchone()
        assert campaign and campaign['status']=='draft' and campaign['request_id']==req
        campaign_id=campaign['id']
    buyer_dash=c.get('/dashboard').text; buyer_csrf=re.search(r'name="csrf" value="([^"]+)',buyer_dash).group(1)
    assert 'Spring Launch' in buyer_dash
    assert f'/advertiser/campaigns/{campaign_id}/analytics' in buyer_dash
    # v2.3: advertiser-owned daily metrics, derived KPIs, CSV export and ownership isolation.
    metric_page=c.get(f'/advertiser/campaigns/{campaign_id}/analytics')
    assert metric_page.status_code==200 and 'Пока нет статистики' in metric_page.text
    metric_csrf=re.search(r'name=\"csrf\" value=\"([^\"]+)',metric_page.text).group(1)
    metric_post=c.post(f'/advertiser/campaigns/{campaign_id}/analytics',data={'csrf':metric_csrf,'metric_date':'2026-11-12','spend':'1000','impressions':'10000','clicks':'250','conversions':'10','revenue':'2500','note':'Manual test snapshot'},follow_redirects=False)
    assert metric_post.status_code==303
    metric_page=c.get(f'/advertiser/campaigns/{campaign_id}/analytics')
    assert '2.50%' in metric_page.text and '4.00 ₽' in metric_page.text and '150.00%' in metric_page.text
    assert 'Manual test snapshot' in metric_page.text
    # v2.4: strict CSV import, idempotent date upsert and source attribution.
    metric_csrf=re.search(r'name=\"csrf\" value=\"([^\"]+)',metric_page.text).group(1)
    csv_bytes=b'date,spend,impressions,clicks,conversions,revenue,note\n2026-11-13,1500,12000,300,12,4000,Imported report\n'
    imported=c.post(f'/advertiser/campaigns/{campaign_id}/analytics/import',data={'csrf':metric_csrf},files={'file':('metrics.csv',csv_bytes,'text/csv')},follow_redirects=False)
    assert imported.status_code==303 and 'imported=1' in imported.headers['location'], imported.text
    metric_page=c.get(f'/advertiser/campaigns/{campaign_id}/analytics')
    assert 'CSV-импорт' in metric_page.text and 'Imported report' in metric_page.text
    with db.connect() as con:
        imported_row=con.execute("SELECT source,spend FROM campaign_metric_snapshots WHERE campaign_id=? AND metric_date='2026-11-13'",(campaign_id,)).fetchone()
        assert imported_row['source']=='import' and imported_row['spend']==1500
    bad_csv=b'date,spend,impressions,clicks,conversions\n2026-11-14,100,10,20,1\n'
    bad_import=c.post(f'/advertiser/campaigns/{campaign_id}/analytics/import',data={'csrf':metric_csrf},files={'file':('bad.csv',bad_csv,'text/csv')})
    assert bad_import.status_code==400
    metric_csv=c.get(f'/advertiser/campaigns/{campaign_id}/analytics.csv')
    assert metric_csv.status_code==200 and '2026-11-12,1000,10000,250,10,2500,manual' in metric_csv.text
    with db.connect() as con:
        assert con.execute('SELECT COUNT(*) FROM campaign_metric_snapshots WHERE campaign_id=?',(campaign_id,)).fetchone()[0]==2
    with TestClient(app) as other_buyer:
        other_html=other_buyer.get('/register').text; other_csrf=re.search(r'name=\"csrf\" value=\"([^\"]+)',other_html).group(1)
        other_buyer.post('/register',data={'csrf':other_csrf,'email':'otherbuyer@example.test','password':'StrongPassword123','role':'advertiser','name':'Other Buyer','company':'Other Co'},follow_redirects=False)
        other_link=sent[-1][2]; other_token=other_link.split('token=')[1]; other_buyer.get('/verify-email',params={'token':other_token})
        other_html=other_buyer.get('/login').text; other_csrf=re.search(r'name=\"csrf\" value=\"([^\"]+)',other_html).group(1)
        other_buyer.post('/login',data={'csrf':other_csrf,'email':'otherbuyer@example.test','password':'StrongPassword123'},follow_redirects=False)
        assert other_buyer.get(f'/advertiser/campaigns/{campaign_id}/analytics').status_code==404
        other_dash=other_buyer.get('/dashboard').text
        other_csrf=re.search(r'name=\"csrf\" value=\"([^\"]+)',other_dash).group(1)
        denied_import=other_buyer.post(f'/advertiser/campaigns/{campaign_id}/analytics/import',data={'csrf':other_csrf},files={'file':('metrics.csv',csv_bytes,'text/csv')})
        assert denied_import.status_code==404
    changed=c.post(f'/advertiser/campaigns/{campaign_id}/status',data={'csrf':buyer_csrf,'status':'active'},follow_redirects=False)
    assert changed.status_code==303
    with db.connect() as con:
        assert con.execute('SELECT status FROM campaigns WHERE id=?',(campaign_id,)).fetchone()['status']=='active'
        assert con.execute("SELECT COUNT(*) FROM campaign_events WHERE campaign_id=?",(campaign_id,)).fetchone()[0]==2
    planner=c.get('/media-planner')
    assert planner.status_code==200 and 'Новый медиаплан' in planner.text
    plan_csrf=re.search(r'name="csrf" value="([^"]+)',planner.text).group(1)
    saved=c.post('/advertiser/media-plans/generate',data={'csrf':plan_csrf,'title':'Spring media plan','business':'Tech services','goal':'Продвижение технологий для бизнеса','audience':'Малый бизнес','geo':'Россия','budget':'25000','channels':'VK, Сайт / медиа'},follow_redirects=False)
    assert saved.status_code==303 and 'created=' in saved.headers['location']
    with db.connect() as con:
        saved_plan=con.execute("SELECT * FROM media_plans WHERE advertiser_id=? AND title='Spring media plan'",(uid,)).fetchone()
        assert saved_plan and json.loads(saved_plan['plan_json'])['budget']==25000
        saved_plan_id=saved_plan['id']
    planner=c.get('/media-planner')
    assert 'Spring media plan' in planner.text
    plan_csrf=re.search(r'name="csrf" value="([^"]+)',planner.text).group(1)
    attached=c.post(f'/advertiser/media-plans/{saved_plan_id}/attach',data={'csrf':plan_csrf,'campaign_id':campaign_id},follow_redirects=False)
    assert attached.status_code==303
    with db.connect() as con:
        assert con.execute('SELECT status FROM media_plans WHERE id=?',(saved_plan_id,)).fetchone()['status']=='applied'
        assert con.execute('SELECT media_plan_json FROM campaigns WHERE id=?',(campaign_id,)).fetchone()['media_plan_json']
        assert con.execute("SELECT COUNT(*) FROM campaign_events WHERE campaign_id=? AND event_type='media_plan_attached'",(campaign_id,)).fetchone()[0]==1
    # v1.3: supplier must submit HTTPS publication proof; advertiser completion snapshots acceptance.
    with db.connect() as con:
        req2=con.execute("INSERT INTO advertiser_requests(advertiser_id,title,goal,geo,budget) VALUES(?,?,?,?,?)",(uid,'Proof campaign','Leads','RU',5000)).lastrowid
        resp2=con.execute("INSERT INTO request_responses(request_id,supplier_id,proposed_price,status) VALUES(?,?,?,'accepted')",(req2,supplier,1000)).lastrowid
        placement2=con.execute("INSERT INTO placements(response_id,status) VALUES(?,'confirmed')",(resp2,)).lastrowid
        con.commit()
    with TestClient(app) as sc:
        sh=sc.get('/login').text; scsrf=re.search(r'name="csrf" value="([^"]+)',sh).group(1)
        assert sc.post('/login',data={'csrf':scsrf,'email':'s@example.test','password':'StrongPassword123'},follow_redirects=False).status_code==303
        sh=sc.get('/dashboard').text
        assert 'Рабочее пространство поставщика' in sh and 'workspace-overview' in sh
        shcsrf=re.search(r'name="csrf" value="([^"]+)',sh).group(1)
        denied=sc.post(f'/advertiser/campaigns/{campaign_id}/status',data={'csrf':shcsrf,'status':'paused'},follow_redirects=False)
        assert denied.status_code==403, denied.text
        scsrf=re.search(r'name="csrf" value="([^"]+)',sh).group(1)
        assert sc.post(f'/supplier/placements/{placement2}/published',data={'csrf':scsrf,'publication_url':'http://example.test/post'},follow_redirects=False).status_code==400
        assert sc.post(f'/supplier/placements/{placement2}/published',data={'csrf':scsrf,'publication_url':'https://example.test/post'},follow_redirects=False).status_code==303
    buyer_dash=c.get('/dashboard').text; buyer_csrf=re.search(r'name="csrf" value="([^"]+)',buyer_dash).group(1)
    assert c.post(f'/advertiser/placements/{placement2}/complete',data={'csrf':buyer_csrf},follow_redirects=False).status_code==303
    with db.connect() as con:
        receipt=con.execute('SELECT status,accepted_by,publication_url FROM placement_delivery_receipts WHERE placement_id=?',(placement2,)).fetchone()
        assert receipt['status']=='accepted' and receipt['accepted_by']==uid and receipt['publication_url']=='https://example.test/post'
    dash=c.get('/dashboard').text; csrf=re.search(r'name="csrf" value="([^"]+)',dash).group(1)
    r=c.post(f'/advertiser/placements/{placement}/payments/test-intent',data={'csrf':csrf},follow_redirects=False)
    assert r.status_code==303 and '/payments/test/' in r.headers['location'], (r.status_code,r.text)
    pid=int(r.headers['location'].split('/')[-1])
    page=c.get(r.headers['location']).text; csrf=re.search(r"name='csrf' value='([^']+)",page).group(1)
    r=c.post(f'/payments/test/{pid}/simulate',data={'csrf':csrf,'result':'success'},follow_redirects=False)
    assert r.status_code==303, r.text
    wallet=c.get('/wallet').text; assert 'sandbox_payment' in wallet or 'эскроу' in wallet.lower() or 'Sandbox' in wallet
    with db.connect() as con:
        payrow=con.execute('SELECT escrow_status FROM payment_intents WHERE id=?',(pid,)).fetchone()
    assert payrow['escrow_status']=='held'
    assert c.get('/analytics').status_code==200
    # v2.5: advertiser integration hub; Yandex OAuth token is encrypted at rest.
    integ=c.get('/integrations'); assert integ.status_code==200 and 'VK Реклама' in integ.text
    assert c.get('/api/integrations').json()['providers']['yandex_direct']['sync_enabled'] is False
    from cryptography.fernet import Fernet
    from unittest.mock import patch
    oauth_key=Fernet.generate_key().decode()
    os.environ['YANDEX_DIRECT_CLIENT_ID']='test-client'
    os.environ['YANDEX_DIRECT_CLIENT_SECRET']='test-secret'
    os.environ['YANDEX_DIRECT_REDIRECT_URI']='https://admarket.example/integrations/yandex-direct/callback'
    os.environ['INTEGRATION_ENCRYPTION_KEY']=oauth_key
    integ=c.get('/integrations'); csrf=re.search(r'name="csrf" value="([^"]+)',integ.text).group(1)
    start=c.post('/integrations/yandex-direct/connect',data={'csrf':csrf},follow_redirects=False)
    assert start.status_code==303 and 'oauth.yandex.com/authorize' in start.headers['location']
    from urllib.parse import urlparse,parse_qs
    state=parse_qs(urlparse(start.headers['location']).query)['state'][0]
    class FakeOAuthResponse:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,n=-1): return json.dumps({'access_token':'fake-sensitive-token','expires_in':3600,'scope':'direct:api'}).encode()
    with patch.object(main.urllib.request,'urlopen',return_value=FakeOAuthResponse()):
        callback=c.get('/integrations/yandex-direct/callback',params={'code':'fake-code','state':state},follow_redirects=False)
    assert callback.status_code==303 and 'connected=yandex' in callback.headers['location']
    with db.connect() as con:
        saved=con.execute("SELECT token_ciphertext,status FROM ad_platform_connections WHERE user_id=? AND provider='yandex_direct'",(uid,)).fetchone()
        assert saved['status']=='authorized_pending_access' and 'fake-sensitive-token' not in saved['token_ciphertext']
        assert Fernet(oauth_key.encode()).decrypt(saved['token_ciphertext'].encode()).decode()=='fake-sensitive-token'
        assert con.execute('SELECT COUNT(*) FROM ad_platform_oauth_states').fetchone()[0]==0
    os.environ.pop('YANDEX_DIRECT_CLIENT_ID',None); os.environ.pop('YANDEX_DIRECT_CLIENT_SECRET',None); os.environ.pop('YANDEX_DIRECT_REDIRECT_URI',None); os.environ.pop('INTEGRATION_ENCRYPTION_KEY',None)
    with db.connect() as con:
        webhook_id=con.execute('SELECT event_id FROM webhook_events WHERE event_type=\"payment.succeeded\"').fetchone()[0]
    webhook_payload={'event_id':webhook_id,'type':'payment.succeeded','payment_id':pid}
    webhook_raw=json.dumps(webhook_payload,separators=(',',':'),sort_keys=True).encode()
    assert c.post('/webhooks/sandbox',content=webhook_raw,headers={'X-ADMARKET-SIGNATURE':'bad'}).status_code==401
    webhook_sig=hmac.new(os.environ['SANDBOX_WEBHOOK_SECRET'].encode(),webhook_raw,hashlib.sha256).hexdigest()
    webhook_response=c.post('/webhooks/sandbox',content=webhook_raw,headers={'X-ADMARKET-SIGNATURE':webhook_sig})
    assert webhook_response.status_code==200 and webhook_response.json()['result']=='duplicate'
    # Exercise actual password reset token delivery and single-use reset.
    forgot=c.get('/forgot-password').text; csrf=re.search(r"name='csrf' value='([^']+)",forgot).group(1)
    assert c.post('/forgot-password',data={'csrf':csrf,'email':'buyer@example.test'}).status_code==200
    reset_token=sent[-1][2].split('token=')[1]
    reset_page=c.get('/reset-password',params={'token':reset_token}).text; csrf=re.search(r"name='csrf' value='([^']+)",reset_page).group(1)
    assert c.post('/reset-password',data={'csrf':csrf,'token':reset_token,'password':'AnotherStrongPassword456'}).status_code==200
    assert c.get('/reset-password',params={'token':reset_token}).status_code==400
    # Review by an advertiser is allowed only for a completed placement and only once.
    dash=c.get('/dashboard').text; csrf=re.search(r'name="csrf" value="([^"]+)',dash).group(1)
    rr=c.post(f'/placements/{placement}/reviews',data={'csrf':csrf,'rating':'5','body':'Good placement'},follow_redirects=False)
    assert rr.status_code==303, rr.text
    # Non-admin cannot access admin console.
    assert c.get('/admin').status_code==403
    with db.connect() as con:
        con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified) VALUES('impostor@example.test',?,'admin','Impostor','',1)",(hash_password('StrongPassword123'),))
    with TestClient(app) as impostor:
        loginhtml=impostor.get('/login').text; csrf=re.search(r'name=\"csrf\" value=\"([^\"]+)',loginhtml).group(1)
        impostor.post('/login',data={'csrf':csrf,'email':'impostor@example.test','password':'StrongPassword123'},follow_redirects=False)
        assert impostor.get('/admin').status_code==403
        assert impostor.get('/admin/quality.json').status_code==403
    with db.connect() as con:
        admin_id=con.execute("INSERT INTO users(email,password_hash,role,name,company,email_verified) VALUES('admin@example.com',?,'admin','Admin','',1)",(hash_password('StrongPassword123'),)).lastrowid
    with TestClient(app) as a:
        loginhtml=a.get('/login').text; csrf=re.search(r'name="csrf" value="([^"]+)',loginhtml).group(1)
        rr=a.post('/login',data={'csrf':csrf,'email':'admin@example.com','password':'StrongPassword123'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        assert a.get('/admin').status_code==200
        assert a.get('/admin/reconciliation').status_code==200
        # v1.5: admin-only quality report, persisted score snapshots and deduplicated anti-fraud scan.
        assert a.get('/admin/quality.json').status_code==200
        quality_data=a.get('/admin/quality.json').json()
        assert quality_data['version']=='2.8.0' and isinstance(quality_data['suppliers'],list)
        admin_dash=a.get('/admin').text; csrf=re.search(r'name=\"csrf\" value=\"([^\"]+)',admin_dash).group(1)
        rr=a.post('/admin/quality/recalculate',data={'csrf':csrf},follow_redirects=False)
        assert rr.status_code==303
        rr=a.post('/admin/fraud/scan',data={'csrf':csrf},follow_redirects=False)
        assert rr.status_code==303
        with db.connect() as con:
            assert con.execute('SELECT COUNT(*) FROM supplier_quality_snapshots').fetchone()[0]>=1
        # v1.4: advertiser complaint workflow and admin SLA breach scan.
        buyer_dash=c.get('/dashboard').text; buyer_csrf=re.search(r'name="csrf" value="([^"]+)',buyer_dash).group(1)
        rr=c.post(f'/placements/{placement}/complaints',data={'csrf':buyer_csrf,'category':'quality','description':'The delivered publication quality did not match the agreed brief.'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        with db.connect() as con:
            complaint_id=con.execute('SELECT id FROM supplier_complaints WHERE placement_id=?',(placement,)).fetchone()[0]
            con.execute("INSERT INTO supplier_sla_cases(placement_id,supplier_id,due_at) VALUES(?,?, '2020-01-01 00:00:00')",(placement,supplier))
            con.commit()
        admin_dash=a.get('/admin').text; csrf=re.search(r'name="csrf" value="([^"]+)',admin_dash).group(1)
        assert f'#{complaint_id}' in admin_dash
        rr=a.post(f'/admin/complaints/{complaint_id}/decision',data={'csrf':csrf,'status':'resolved','resolution':'Reviewed evidence and closed the complaint.'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        rr=a.post('/admin/sla/scan',data={'csrf':csrf},follow_redirects=False)
        assert rr.status_code==303, rr.text
        with db.connect() as con:
            assert con.execute('SELECT status FROM supplier_complaints WHERE id=?',(complaint_id,)).fetchone()[0]=='resolved'
            assert con.execute('SELECT status FROM supplier_sla_cases WHERE placement_id=?',(placement,)).fetchone()[0]=='breached'
        admin_dash=a.get('/admin').text; csrf=re.search(r'name="csrf" value="([^"]+)',admin_dash).group(1)
        rr=a.post(f'/admin/suppliers/{supplier}/verification',data={'csrf':csrf,'status':'verified','note':'Tested identity docs'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        with db.connect() as con: assert con.execute('SELECT supplier_verification FROM users WHERE id=?',(supplier,)).fetchone()[0]=='verified'
        # Open a dispute as the advertiser and attach both a link and a private image file.
        buyer_dash=c.get('/dashboard').text; buyer_csrf=re.search(r'name="csrf" value="([^"]+)',buyer_dash).group(1)
        rr=c.post(f'/placements/{placement}/disputes',data={'csrf':buyer_csrf,'reason':'The placement evidence does not match the agreed deliverables.'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        with db.connect() as con: dispute_id=con.execute('SELECT id FROM disputes WHERE placement_id=?',(placement,)).fetchone()[0]
        rr=c.post(f'/disputes/{dispute_id}/evidence/link',data={'csrf':buyer_csrf,'url':'https://example.test/evidence','note':'Reference link'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        png=b'\x89PNG\r\n\x1a\n'+b'\x00'*24
        rr=c.post(f'/disputes/{dispute_id}/evidence/file',data={'csrf':buyer_csrf,'note':'Screenshot'},files={'file':('proof.png',png,'image/png')},follow_redirects=False)
        assert rr.status_code==303, rr.text
        evidence_id=None
        with db.connect() as con: evidence_id=con.execute("SELECT id FROM dispute_evidence WHERE dispute_id=? AND evidence_type='file'",(dispute_id,)).fetchone()[0]
        assert c.get(f'/disputes/evidence/{evidence_id}').status_code==200
        admin_dash=a.get('/admin').text; csrf=re.search(r'name="csrf" value="([^"]+)',admin_dash).group(1)
        assert f'Спор #{dispute_id}' in admin_dash
        rr=a.post(f'/admin/payments/{pid}/release',data={'csrf':csrf,'note':'Must be blocked while disputed'},follow_redirects=False)
        assert rr.status_code==409, rr.text
        rr=a.post(f'/admin/disputes/{dispute_id}/decision',data={'csrf':csrf,'decision':'resolved_for_supplier','resolution':'Evidence reviewed; placement completion accepted.'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        admin_dash=a.get('/admin').text; csrf=re.search(r'name="csrf" value="([^"]+)',admin_dash).group(1)
        rr=a.post(f'/admin/payments/{pid}/release',data={'csrf':csrf,'note':'Smoke release after decision'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        rr=a.post(f'/admin/payments/{pid}/release',data={'csrf':csrf,'note':'Duplicate attempt'},follow_redirects=False)
        assert rr.status_code==409, rr.text
        with db.connect() as con:
            assert con.execute('SELECT escrow_status FROM payment_intents WHERE id=?',(pid,)).fetchone()[0]=='released'
            assert con.execute('SELECT SUM(amount) FROM ledger_entries WHERE account_type=\"user\" AND user_id=?',(supplier,)).fetchone()[0]==880
        admin_dash=a.get('/admin').text; csrf=re.search(r'name="csrf" value="([^"]+)',admin_dash).group(1)
        rr=a.post(f'/admin/payments/{pid}/refund',data={'csrf':csrf,'reason':'Test refund'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        with db.connect() as con:
            self_status=con.execute('SELECT status FROM payment_intents WHERE id=?',(pid,)).fetchone()[0]
            self_total=con.execute("SELECT SUM(amount) FROM ledger_entries WHERE account_type='user' AND user_id=?",(supplier,)).fetchone()[0]
        assert self_status=='refunded' and self_total==0, (self_status,self_total)
        report=a.get('/admin/reconciliation').json()
        assert report['ok'], report['issues']
        # v1.6: supplier-submitted platform profile, immutable metric snapshots and admin review.
        with TestClient(app) as supplier_client:
            supplier_login=supplier_client.get('/login').text; supplier_csrf=re.search(r"name=\"csrf\" value=\"([^\"]+)",supplier_login).group(1)
            assert supplier_client.post('/login',data={'csrf':supplier_csrf,'email':'s@example.test','password':'StrongPassword123'},follow_redirects=False).status_code==303
            supplier_dash=supplier_client.get('/dashboard').text
            assert 'Проверка рекламных площадок' in supplier_dash
            supplier_csrf=re.search(r"name=\"csrf\" value=\"([^\"]+)",supplier_dash).group(1)
            rr=supplier_client.post('/supplier/platform-accounts',data={'csrf':supplier_csrf,'platform':'VK','handle':'club_test','profile_url':'https://vk.com/club_test'},follow_redirects=False)
            assert rr.status_code==303, rr.text
            with db.connect() as con: account_id=con.execute("SELECT id FROM platform_accounts WHERE handle='club_test'").fetchone()[0]
            rr=supplier_client.post(f'/supplier/platform-accounts/{account_id}/metrics',data={'csrf':supplier_csrf,'followers':10000,'avg_views':5000,'avg_reach':7000,'engagement_rate':4.5,'evidence_url':'https://example.test/stats'},follow_redirects=False)
            assert rr.status_code==303, rr.text
            rr=supplier_client.post(f'/supplier/platform-accounts/{account_id}/metrics',data={'csrf':supplier_csrf,'followers':20000,'avg_views':6000,'avg_reach':9000,'engagement_rate':5.0},follow_redirects=False)
            assert rr.status_code==303, rr.text
        admin_dash=a.get('/admin').text; csrf=re.search(r"name=\"csrf\" value=\"([^\"]+)",admin_dash).group(1)
        assert 'club_test' in admin_dash
        assert a.get('/admin/platform-trust.json').status_code==200
        assert a.get('/admin/platform-trust.json').json()['version']=='2.8.0'
        rr=a.post('/admin/platform-trust/scan',data={'csrf':csrf},follow_redirects=False)
        assert rr.status_code==303, rr.text
        with db.connect() as con:
            assert con.execute("SELECT COUNT(*) FROM platform_trust_signals WHERE signal_type='follower_growth'").fetchone()[0]>=1
            signal_id=con.execute("SELECT id FROM platform_trust_signals WHERE signal_type='follower_growth' LIMIT 1").fetchone()[0]
        rr=a.post(f'/admin/platform-trust-signals/{signal_id}/status',data={'csrf':csrf,'status':'reviewed','note':'Reviewed sample data'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        rr=a.post(f'/admin/platform-accounts/{account_id}/verification',data={'csrf':csrf,'status':'verified','note':'Manual profile review'},follow_redirects=False)
        assert rr.status_code==303, rr.text
        with db.connect() as con:
            assert con.execute('SELECT verification_status FROM platform_accounts WHERE id=?',(account_id,)).fetchone()[0]=='verified'
            assert con.execute('SELECT status FROM platform_trust_signals WHERE id=?',(signal_id,)).fetchone()[0]=='reviewed'

    print('INTEGRATION_OK: v2.2 saved AI media plans + campaign attachment; v2.1 campaign control + v2.0 role workspaces; v1.6 platform profiles/metrics/immutability/trust signals/admin review; auth/reset, role checks, signed webhook/idempotency, sandbox escrow, disputes, private evidence upload/link/access, admin decision, payout freeze/idempotency, analytics, publication proof acceptance, review and refund reversal')
tmp.cleanup()
