document.addEventListener("DOMContentLoaded", () => {
  const search = document.getElementById("search");
  const channel = document.getElementById("channel");
  const geo = document.getElementById("geo");
  const maxPrice = document.getElementById("max-price");
  const minAudience = document.getElementById("min-audience");
  const sort = document.getElementById("sort");
  const cards = [...document.querySelectorAll("#cards .card")];
  const count = document.getElementById("catalog-count");
  const comparePanel = document.getElementById("compare-panel");
  const selected = new Set();
  const money = value => Number(value).toLocaleString("ru-RU") + " ₽";
  function renderCompare() {
    const chosen = cards.filter(card => selected.has(card));
    if (!comparePanel) return;
    comparePanel.hidden = chosen.length === 0;
    comparePanel.replaceChildren();
    if (!chosen.length) return;
    const heading = document.createElement("div"); heading.className = "compare-heading";
    const title = document.createElement("strong"); title.textContent = `Сравнение площадок · ${chosen.length}/3`;
    const clear = document.createElement("button"); clear.type = "button"; clear.className = "small-button"; clear.textContent = "Очистить";
    clear.addEventListener("click", () => { selected.clear(); cards.forEach(c => { const b=c.querySelector('.compare-toggle'); if(b){b.setAttribute('aria-pressed','false');b.textContent='＋ Сравнить';} }); renderCompare(); });
    heading.append(title, clear); comparePanel.append(heading);
    const table = document.createElement("table");
    const fields = [["Площадка", c => c.dataset.name], ["Канал", c => c.dataset.channel], ["Тематика", c => c.dataset.topic], ["География", c => c.dataset.geo], ["Аудитория", c => Number(c.dataset.audience).toLocaleString("ru-RU")], ["Цена", c => money(c.dataset.price)], ["Форматы", c => c.dataset.formats]];
    const body = document.createElement("tbody");
    fields.forEach(([label, get]) => { const tr=document.createElement("tr"); const th=document.createElement("th"); th.textContent=label; tr.append(th); chosen.forEach(card=>{const td=document.createElement('td');td.textContent=get(card);tr.append(td);}); body.append(tr); });
    table.append(body); const wrap=document.createElement('div');wrap.className='table-wrap';wrap.append(table);comparePanel.append(wrap);
  }
  function filter() {
    const q = (search?.value || "").trim().toLowerCase();
    const c = channel?.value || ""; const g = geo?.value || "";
    const p = Number(maxPrice?.value || 0); const a = Number(minAudience?.value || 0);
    let visible = cards.filter(card => {
      const matches = (!q || card.dataset.search.includes(q)) && (!c || card.dataset.channel === c) &&
        (!g || card.dataset.geo === g || card.dataset.geo === "Россия") &&
        (!p || Number(card.dataset.price) <= p) && (!a || Number(card.dataset.audience) >= a);
      card.hidden = !matches; return matches;
    });
    const mode=sort?.value || 'recommended';
    visible.sort((x,y)=> mode==='price-asc' ? Number(x.dataset.price)-Number(y.dataset.price) : mode==='price-desc' ? Number(y.dataset.price)-Number(x.dataset.price) : mode==='audience-desc' ? Number(y.dataset.audience)-Number(x.dataset.audience) : 0);
    const container=document.getElementById('cards'); visible.forEach(card=>container.append(card));
    if(count) count.textContent=`${visible.length} площадок найдено`;
  }
  [search,channel,geo,maxPrice,minAudience,sort].forEach(el=>el?.addEventListener('input',filter));
  sort?.addEventListener('change',filter);
  cards.forEach(card=>{const button=card.querySelector('.compare-toggle');if(!button)return;button.addEventListener('click',()=>{
    if(selected.has(card)){selected.delete(card);button.setAttribute('aria-pressed','false');button.textContent='＋ Сравнить';}
    else if(selected.size<3){selected.add(card);button.setAttribute('aria-pressed','true');button.textContent='✓ Добавлено';}
    else {button.textContent='Максимум 3';setTimeout(()=>{if(!selected.has(card))button.textContent='＋ Сравнить';},1100);}
    renderCompare();
  });});
  filter();
});

// AI chat with conversation history and structured brief context.
document.addEventListener("DOMContentLoaded", () => {
  const form=document.getElementById("ai-form"), input=document.getElementById("ai-message");
  const log=document.getElementById("chat-log"), button=document.getElementById("ai-send");
  if(!form)return;
  const history=[];
  const brief={business:"",goal:"",audience:"",geo:"Россия",budget:30000,channels:[]};
  function bubble(text,role){const el=document.createElement("div");el.className="bubble "+role;el.textContent=text;log.appendChild(el);log.scrollTop=log.scrollHeight;}
  form.addEventListener("submit",async e=>{
    e.preventDefault();const message=input.value.trim();if(!message)return;
    bubble(message,"user");input.value="";button.disabled=true;button.textContent="Думаю...";
    try{
      const response=await fetch("/api/ai",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message,history,brief})});
      const data=await response.json();const reply=data.reply||"Не получил ответ.";
      history.push({role:"user",content:message},{role:"assistant",content:reply});bubble(reply,"bot");
    }catch(err){bubble("Не удалось связаться с сервером. Проверь, запущен ли ADMARKET.","bot");}
    finally{button.disabled=false;button.textContent="Отправить ↗";}
  });
});

// Deterministic budget plan from the current inventory database.
document.addEventListener("DOMContentLoaded",()=>{
  const form=document.getElementById("plan-form"),out=document.getElementById("plan-result");if(!form)return;
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  form.addEventListener("submit",async e=>{
    e.preventDefault();const fd=new FormData(form);
    const channels=[...form.querySelector('[name="channels"]').selectedOptions].map(x=>x.value);
    const body={business:fd.get("business"),goal:fd.get("goal"),audience:fd.get("audience"),geo:fd.get("geo"),budget:Number(fd.get("budget")),channels};
    out.innerHTML='<div class="notice">Собираю медиаплан...</div>';
    try{
      const res=await fetch('/api/plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const p=await res.json();
      if(!p.items||!p.items.length){out.innerHTML='<div class="notice">Подходящих площадок не найдено. Попробуй изменить вводные или расширить бюджет.</div>';return;}
      const rows=p.items.map(x=>`<tr><td>${esc(x.name)}</td><td>${esc(x.channel)}</td><td>${Number(x.price).toLocaleString('ru-RU')} ₽</td><td>${Number(x.audience).toLocaleString('ru-RU')}</td></tr>`).join('');
      out.innerHTML=`<div class="plan-card"><div class="eyebrow">ПРЕДВАРИТЕЛЬНЫЙ РАСЧЁТ</div><h2>Твой медиаплан</h2><div class="table-wrap"><table><thead><tr><th>Площадка</th><th>Канал</th><th>Тестовая цена</th><th>Аудитория*</th></tr></thead><tbody>${rows}</tbody></table></div><div class="totals"><div><small>Размещения</small><strong>${p.media_total.toLocaleString('ru-RU')} ₽</strong></div><div><small>Комиссия (12%*)</small><strong>${p.commission.toLocaleString('ru-RU')} ₽</strong></div><div><small>Итого</small><strong>${p.total.toLocaleString('ru-RU')} ₽</strong></div><div><small>Остаток бюджета</small><strong>${p.remaining.toLocaleString('ru-RU')} ₽</strong></div></div><p class="disclaimer">${esc(p.warning)} *Аудитория не проверена. Комиссия 12% — гипотеза прототипа.</p></div>`;
    }catch(err){out.innerHTML='<div class="notice">Ошибка расчёта. Проверь сервер и повтори.</div>';}
  });
});
