/* Games & features layer under each release: shared by every release timeline
   (timeline.py → iGaming, Cloud Services; v2_timeline.py → V2). Inlined into the
   page at build time (the PIPELINE_JS placeholder). Edit here, never in a template. */
// ─── GAMES & FEATURES LAYER ─────────────────────────────────────────────────
// Each release's Scope cards (epics; Stories rolled up) as nested rows, split
// into games and features, with a forecast per discipline lane. Same rule as
// the release bar (calcDevEnd / calcTypeEnd): every person works through their
// open hours at hpd() from today; dev people first finish their dated work in
// higher-priority releases (getDynamicQueued). Inside the release a person
// takes its games/features in the order shown (due date, games before
// features), so the last item each person finishes lands exactly on the
// release's dev end: the layer always adds up to the release bar (checked in
// plRender; a mismatch shows on the toggle as ⚠).
const PL_LANES=[
  {k:'design',lbl:'Design',type:'Design',  color:'#0891b2'},
  {k:'art',   lbl:'Art',   type:'Creative',color:'#7c3aed'},
  {k:'math',  lbl:'Math',  type:'Math',    color:'#0d9488'},
  {k:'dev',   lbl:'Dev',   type:null,      color:'#2563eb'},
  {k:'sound', lbl:'Sound', type:'Sound',   color:'#db2777'},
];
const PL_KIND={game:{o:0,icon:'🎮',grp:'Games'},feature:{o:1,icon:'◆',grp:'Features'},
  release:{o:2,icon:'⚙',grp:'Release prep'},other:{o:3,icon:'·',grp:'Other work'},scenario:{o:4,icon:'⚡',grp:'Scenario'}};
// Browser-storage prefix: DASH.ls on the shared engine, 'v2_' on V2's page.
const PL_KEY=(typeof DASH!=='undefined'?DASH.ls:'v2_')+'pipe_open';
let PL_OPEN=new Set();
try{PL_OPEN=new Set(JSON.parse(localStorage.getItem(PL_KEY)||'[]'));}catch(e){}
function plSave(){try{localStorage.setItem(PL_KEY,JSON.stringify([...PL_OPEN]));}catch(e){}}
function plLaneOf(p){
  if(!p.type) return 'dev';
  const l=PL_LANES.find(x=>x.type&&p.type.startsWith(x.type));
  return l?l.k:null;
}
function plCounts(fv){
  const c={game:0,feature:0};
  (fv.scope||[]).forEach(s=>{if(s.kind==='game')c.game++;else if(s.kind==='feature')c.feature++;});
  return c;
}
function plAdd(it,lane,p,tasks,cum,rate){
  const L=it.lanes[lane]||(it.lanes[lane]={n:0,done:0,openN:0,openH:0,spentH:0,people:new Set(),start:null,end:null,firstC:null,lastR:null});
  for(const t of tasks){
    L.n++; L.spentH+=t.spentH||0;
    if(t.created&&(!L.firstC||t.created<L.firstC))L.firstC=t.created;
    if(isDone(t.status)){L.done++;if(t.resolved&&(!L.lastR||t.resolved>L.lastR))L.lastR=t.resolved;}
  }
  const open=tasks.filter(t=>!isDone(t.status));
  if(!open.length) return cum;
  L.people.add(p.name); L.openN+=open.length;
  const h=open.reduce((s,t)=>s+t.hours,0);
  L.openH+=h;
  const st=addWD(TODAY,cum,rate); cum+=h; const en=addWD(TODAY,cum,rate);
  if(!L.start||st<L.start)L.start=st;
  if(!L.end||en>L.end)L.end=en;
  return cum;
}
function plItems(fv,fvIdx){
  const items=(fv.scope||[]).map(s=>({key:s.key,name:s.label||s.name,kind:s.kind||(s.key?'feature':'other'),
    due:s.due||null,epicStatus:s.status,pipe:s.pipe||null,keys:new Set(s.taskKeys||[]),lanes:{}}));
  items.sort((a,b)=>(a.due||'9999').localeCompare(b.due||'9999')
    ||PL_KIND[a.kind].o-PL_KIND[b.kind].o||String(a.name).localeCompare(String(b.name)));
  const scen={key:null,name:'Scenario work',kind:'scenario',due:null,keys:new Set(),lanes:{}};
  const people=[...(fv.devPeople||[]).map(p=>({p,lane:'dev'})),
                ...(fv.otherPeople||[]).map(p=>({p,lane:plLaneOf(p)}))].filter(x=>x.lane);
  for(const {p,lane} of people){
    const rate=hpd(fv.key,p.name);
    // Mirrors calcDevEnd: a release with no estimated dev work left has no
    // devStart and is dev-complete today; its unestimated placeholders don't queue.
    let cum=lane==='dev'&&fv.devStart?getDynamicQueued(fvIdx,p.name):0;
    const seen=new Set();
    for(const it of items){
      const ts=p.tasks.filter(t=>it.keys.has(t.key));
      if(!ts.length) continue;
      ts.forEach(t=>seen.add(t));
      cum=plAdd(it,lane,p,ts,cum,rate);
    }
    const rest=p.tasks.filter(t=>!seen.has(t));
    if(rest.length) cum=plAdd(scen,lane,p,rest,cum,rate);
  }
  if(Object.keys(scen.lanes).length) items.push(scen);
  return items.filter(it=>Object.keys(it.lanes).length);
}
function plStage(it,fv){
  for(const l of PL_LANES){const L=it.lanes[l.k];if(L&&L.openN)return{lbl:l.lbl,color:l.color};}
  if(/^(closed|done)$/i.test(it.epicStatus||'')) return {lbl:'Done',color:'#16a34a'};
  if(/^In QA/.test(fv.statusLabel||'')) return {lbl:'QA · release',color:'#d97706'};
  return {lbl:'Built',color:'#16a34a'};
}
function plRender(nest,fv,fvIdx,devEnd,qaEnd){
  nest.innerHTML='';
  const items=plItems(fv,fvIdx);
  // Adds-up check: the latest dev-lane end must equal the release's dev end.
  const devEnds=items.map(it=>it.lanes.dev&&it.lanes.dev.end).filter(Boolean);
  const maxDev=devEnds.length?new Date(Math.max(...devEnds.map(d=>+d))):null;
  const expect=maxDev&&maxDev>TODAY?maxDev:new Date(TODAY);
  const consistent=!maxDev||expect.toDateString()===new Date(devEnd).toDateString();
  nest.dataset.consistent=String(!!consistent);
  // Forecast ran in due-date order; display groups by kind, keeping that order.
  const openHOf=it=>Object.values(it.lanes).reduce((s,L)=>s+L.openH,0);
  const shown=items.map((it,i)=>({it,i})).sort((a,b)=>PL_KIND[a.it.kind].o-PL_KIND[b.it.kind].o||a.i-b.i).map(x=>x.it);
  let lastKind=null;
  for(const it of shown){
    if(it.kind!==lastKind){
      const grp=shown.filter(x=>x.kind===it.kind);
      const gh=grp.reduce((s,x)=>s+openHOf(x),0);
      const g=document.createElement('div');g.className='pl-group';
      g.innerHTML=`${PL_KIND[it.kind].icon} ${PL_KIND[it.kind].grp} <span class="n">${grp.length} · ${gh.toFixed(gh<10?1:0)}h left</span>`;
      nest.appendChild(g); lastKind=it.kind;
    }
    const lanes=PL_LANES.filter(l=>it.lanes[l.k]);
    const all=lanes.map(l=>it.lanes[l.k]);
    const openH=all.reduce((s,L)=>s+L.openH,0), spentH=all.reduce((s,L)=>s+L.spentH,0);
    const n=all.reduce((s,L)=>s+L.n,0), done=all.reduce((s,L)=>s+L.done,0);
    const pctDone=spentH+openH>0?Math.round(spentH/(spentH+openH)*100):(n?Math.round(done/n*100):0);
    const ends=all.map(L=>L.end).filter(Boolean);
    const end=ends.length?new Date(Math.max(...ends.map(d=>+d))):null;
    const ppl=new Set();all.forEach(L=>L.people.forEach(x=>ppl.add(x)));
    const stg=plStage(it,fv);
    const row=document.createElement('div');row.className='pl-row';
    const nameHtml=it.key?`<a href="${BASE}${it.key}" target="_blank" onclick="event.stopPropagation()">${msEsc(it.name)}</a>`:msEsc(it.name);
    row.innerHTML=`<div class="pl-gutter"></div>
      <div class="pl-label"><div class="pl-name" title="${msEsc(it.name)}">${nameHtml}</div>
        <div class="pl-meta"><span class="pl-stage" style="color:${stg.color};border-color:${stg.color}55;background:${stg.color}14">${stg.lbl}</span>
        ${it.key?`<span>${it.key}</span>`:''}${ppl.size?`<span>· 👥 ${ppl.size}</span>`:''}${it.pipe?`<a class="pl-pipe" href="game-pipeline.html?project=${it.pipe}&focus=${encodeURIComponent(it.key)}" onclick="event.stopPropagation()" title="Open in the ${it.kind==='game'?'game':'feature'} pipeline">· pipeline ↗</a>`:''}</div></div>`;
    const track=document.createElement('div');track.className='fv-track pl-track';
    track.style.width=trackPxWidth()+'px';
    track.style.minHeight=Math.max(38,10+lanes.length*9)+'px';
    SPRINTS.forEach(s=>{const l=document.createElement('div');l.className='sp-line';l.style.left=pct(s.start)+'%';track.appendChild(l);});
    const tl=document.createElement('div');tl.className='today-line';tl.style.left=pct(TODAY)+'%';track.appendChild(tl);
    const top0=Math.max(6,(Math.max(38,10+lanes.length*9)-lanes.length*9)/2);
    lanes.forEach((l,i)=>{
      const L=it.lanes[l.k], top=top0+i*9;
      const tip=`${l.lbl}: ${L.done}/${L.n} done · ${L.openH.toFixed(1)}h left`+(L.people.size?` · ${[...L.people].join(', ')}`:'')+(L.end?` · forecast ${msFmt(L.end)}`:'');
      if(L.firstC&&!L.openN){   // finished lane: green, first ticket created → last resolved
        const a0=D(L.firstC.slice(0,10)), a=a0<CHART_START?CHART_START:a0;
        const b=L.lastR?D(L.lastR.slice(0,10)):new Date(TODAY);
        if(b>a){const s=document.createElement('div');s.className='pl-lane fin';
          s.style.cssText=`top:${top}px;left:${pct(a)}%;width:${Math.max(+pct(b)-(+pct(a)),.2)}%;background:${l.color}`;s.title=tip;track.appendChild(s);}
      }
      if(L.openN&&L.start&&L.end){
        const s=document.createElement('div');s.className='pl-lane';
        s.style.cssText=`top:${top}px;left:${pct(L.start)}%;width:${Math.max(+pct(L.end)-(+pct(L.start)),.35)}%;background:${l.color}`;
        s.title=tip;track.appendChild(s);
      }
    });
    if(end&&openH>=0&&all.some(L=>L.openN)){
      const e=document.createElement('div');e.className='pl-end';e.style.left=pct(end)+'%';
      e.style.top=(top0+((lanes.length-1)*9)/2)+'px';e.textContent=`→ ${msFmt(end)}`;track.appendChild(e);
    }
    let dueHtml='';
    if(it.due){
      const due=D(it.due), late=end&&end>due;
      const m=document.createElement('div');m.className='pl-due'+(late?' late':'');m.style.left=pct(due)+'%';
      m.title=`Due ${msFmt(due)} (Jira due date)`;track.appendChild(m);
      const dd=end?wdDiff(end.toISOString().slice(0,10),it.due):null;
      dueHtml=`<div>Due ${msFmt(due)}${dd===null||dd===0?'':dd>0?` · <span class="late">+${dd}d</span>`:` · <span class="early">${-dd}d early</span>`}</div>`;
    }
    const scroll=document.createElement('div');scroll.className='fv-scroll';scroll.appendChild(track);
    const sum=document.createElement('div');sum.className='pl-summary';
    const noEst=all.reduce((s,L)=>s+L.openN,0)&&openH===0;
    sum.innerHTML=`<div><b>${openH.toFixed(openH<10?1:0)}h</b> left · ${pctDone}%</div>${noEst?`<div class="late">⚠ open, no estimate</div>`:''}
      <div>${end&&all.some(L=>L.openN)?`Done ~${msFmt(end)}`:stg.lbl==='QA · release'?`QA → ${msFmt(qaEnd)}`:'No open work'}</div>${dueHtml}`;
    row.appendChild(scroll); row.appendChild(sum);
    nest.appendChild(row);
    registerScroller(scroll);
  }
  const lg=document.createElement('div');lg.className='pl-legend';
  lg.innerHTML=PL_LANES.map(l=>`<span><i style="background:${l.color}"></i>${l.lbl}</span>`).join('')
    +`<span><i style="background:var(--done);opacity:.55"></i>finished</span><span><i style="border-left:2px dashed #94a3b8;width:0;height:10px"></i>due date</span>`
    +`<span style="margin-left:auto">Forecast = the release bar's rule, item by item. QA happens in the release's QA rounds.</span>`;
  nest.appendChild(lg);
}

// Attach the layer to one release row: a "▸ N games · M features" toggle in
// its label, and the nested block (returned; the caller appends it right
// after the row). Built lazily on first open; open state kept per release.
function plMount(lbl, fv, idx, devEnd, qaEnd){
  if(fv.isScenario) return null;
  const c = plCounts(fv);
  if(!c.game && !c.feature) return null;
  const txt = [c.game ? `${c.game} game${c.game>1?'s':''}` : '',
               c.feature ? `${c.feature} feature${c.feature>1?'s':''}` : ''].filter(Boolean).join(' · ');
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'pl-toggle';
  lbl.appendChild(btn);
  const nest = document.createElement('div');
  nest.className = 'pl-nest';
  nest.style.setProperty('--rc', fv.color);
  const setOpen = open => {
    btn.classList.toggle('open', open);
    nest.style.display = open ? '' : 'none';
    if(open && !nest.dataset.built){ plRender(nest, fv, idx, devEnd, qaEnd); nest.dataset.built = '1'; }
    const warn = nest.dataset.consistent === 'false' ? ' ⚠' : '';
    btn.textContent = `${open ? '▾' : '▸'} ${txt}${warn}`;
  };
  btn.addEventListener('click', e => {
    e.stopPropagation();
    const open = !PL_OPEN.has(fv.key);
    open ? PL_OPEN.add(fv.key) : PL_OPEN.delete(fv.key);
    plSave(); setOpen(open);
  });
  setOpen(PL_OPEN.has(fv.key));
  return nest;
}
