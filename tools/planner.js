/* ---------- route planner ----------
   Plans a trip from a place, address, stop, your location or a point on the map to another, the way a
   journey planner does: walk to a stop, take a bus, change if needed (up to two changes), walk to the
   destination. The search is RAPTOR (round-based: round 1 = direct buses, round 2 = one change, round 3 =
   two changes) over today's timetable, run on the phone. Live arrival times at the stops near the start
   replace the timetable for the first bus, so a later scheduled bus that is running early, or a further stop
   with a sooner bus, can come out on top. Several options are offered, by arrival time, and you pick one. */
const WALK_MS=1.25, DETOUR=1.25, MAX_WALK=1000, MAX_WALK_FAR=1600, XFER_M=300, MAX_ROUNDS=3, XFER_BUF=60;
const walkSec=m=>m*DETOUR/WALK_MS, walkMin=m=>Math.max(1,Math.round(walkSec(m)/60));
const plan={from:null,to:null,edit:null,q:'',leave:null,opts:null,busy:false,pick:null,sel:0,err:null,seq:0};
const planOn=()=>st.panel&&st.tab==='plan';

/* places, streets and addresses (map/places.json), fetched the first time the planner is used */
let PLACES=null, placesReq=null;
function loadPlaces(){
  if(!placesReq) placesReq=fetch('map/places.json?v=__PLACES_VERSION__').then(r=>r.json()).then(j=>{
    const list=[], A=j.l; j.p.forEach(([n,k,la,lo,a])=>list.push({t:'place',name:n,kind:k,lat:la,lon:lo,area:A[a],key:norm(n)}));
    j.s.forEach(([n,a,la,lo])=>list.push({t:'street',name:n,lat:la,lon:lo,area:A[a],key:norm(n)}));
    PLACES={list,streets:j.a.map(([n,a,hs])=>({n,area:A[a],hs,key:norm(n)}))}; return PLACES;
  }).catch(()=>{placesReq=null;return null});
  return placesReq;
}
const AREA_KIND=/^(neighbourhood|city|town|village|hamlet)$/;
const KIND_ICON=k=>AREA_KIND.test(k)?'🏘':/restaurant|bar|cafe|pub|fast_food|ice_cream|biergarten/.test(k)?'☕':/supermarket|convenience|mall|department|marketplace|greengrocer|bakery|butcher|shop|clothes|shoes|books|electronics|hardware|florist|optician|jewel|gift|toys|sports|furniture|beauty|hairdresser|kiosk|chemist|stationery|mobile/.test(k)?'🛍':/hospital|clinic|doctors|dentist|pharmacy|health|veterinary/.test(k)?'⚕️':/school|university|college|kindergarten|library/.test(k)?'🎓':/museum|theatre|cinema|arts|gallery|attraction|monument|castle|memorial|ruins|historic/.test(k)?'🏛':/hotel|hostel|guest_house|apartment/.test(k)?'🛏':/place_of_worship|church|cathedral/.test(k)?'⛪':/park|garden|playground|pitch|sports_centre|stadium|swimming|fitness/.test(k)?'🌳':/station|bus|train|taxi/.test(k)?'🚉':/townhall|police|post_office|courthouse|government|bank/.test(k)?'🏢':'📍';
const STREET_WORDS=/^(calle|c\/|avenida|avda\.?|av\.?|paseo|p\.?º|plaza|pza\.?|camino|carretera|ctra\.?|glorieta|ronda|travesia|bulevar)\s+/;
const stripSt=s=>s.replace(STREET_WORDS,'').replace(/^(de|del|de la|de los|de las)\s+/,'');
function planSearch(q){
  const nq=norm(q.trim()); if(nq.length<2) return [];
  const out=[], words=nq.split(/\s+/);
  const score=k=>k.startsWith(nq)?0:k.includes(' '+nq)?1:k.includes(nq)?2:words.every(w=>k.includes(w))?3:9;
  /* an address: street name followed by a number */
  const m=nq.match(/^(.*?)[\s,]+n?º?\s*(\d+[a-z]?)$/);
  if(PLACES&&m){ const sk=stripSt(m[1]);
    for(const s of PLACES.streets){ if(!stripSt(s.key).includes(sk)) continue;
      const h=findNumber(s.hs,m[2]);
      if(h) out.push({t:'addr',name:`${s.n} ${h[0]}`,area:s.area,lat:h[1],lon:h[2],sc:stripSt(s.key).startsWith(sk)?-2:-1}); } }
  for(const s of S){ if(!stopRefs[s.i].length) continue; const k=norm(s.name), sc=Math.min(score(k),s.code.toLowerCase()===nq||s.code.slice(2).replace(/^0+/,'')===nq?0:9);
    if(sc<9) out.push({t:'stop',name:s.name,code:s.code,lat:s.lat,lon:s.lon,stop:s.i,sc:sc+.5}); }
  if(PLACES) for(const p of PLACES.list){ const sc=score(p.key); if(sc<9) out.push(Object.assign({},p,{sc:sc+(p.t==='street'?.2:AREA_KIND.test(p.kind)?-.3:0)})); }
  out.sort((a,b)=>a.sc-b.sc||a.name.length-b.name.length);
  /* places out of reach of the buses (the villages around Burgos) go below those near a stop */
  const top=out.slice(0,80); top.forEach(x=>{ if(x.t!=='stop'&&!nearStops(x.lat,x.lon,MAX_WALK_FAR).length) x.sc+=1.5; });
  top.sort((a,b)=>a.sc-b.sc||a.name.length-b.name.length);
  /* the same place mapped twice (a shop and its building, a stop on both sides of the road) shows once */
  const res=[]; for(const x of top){ if(res.some(y=>y.name===x.name&&distM(y.lat,y.lon,x.lat,x.lon)<(x.t==='stop'?150:y.area===x.area?1000:60))) continue; res.push(x); if(res.length>=12) break; }
  return res;
}
/* a house number on a street: the mapped one, or if it isn't mapped, a point between its neighbours on the
   same side of the street (odd or even), or else the nearest mapped number on that side */
function findNumber(hs,want){
  const ex=hs.find(x=>norm(x[0])===want)||hs.find(x=>parseInt(x[0])===parseInt(want)); if(ex) return ex;
  const n=parseInt(want), side=hs.map(x=>[parseInt(x[0]),x]).filter(([k])=>k%2===n%2);
  let lo=null,hi=null; for(const [k,x] of side){ if(k<n&&(!lo||k>lo[0])) lo=[k,x]; if(k>n&&(!hi||k<hi[0])) hi=[k,x]; }
  if(lo&&hi&&hi[0]-lo[0]<=40&&distM(lo[1][1],lo[1][2],hi[1][1],hi[1][2])<600){ const f=(n-lo[0])/(hi[0]-lo[0]);
    return [want,lo[1][1]+(hi[1][1]-lo[1][1])*f,lo[1][2]+(hi[1][2]-lo[1][2])*f]; }
  const near=[lo,hi].filter(x=>x&&Math.abs(x[0]-n)<=20).sort((a,b)=>Math.abs(a[0]-n)-Math.abs(b[0]-n))[0];
  return near?near[1]:null;
}
/* a point on the map gets the name of the nearest address or street */
function nameNear(lat,lon){
  if(!PLACES) return T('pointMap'); let best=null;
  for(const s of PLACES.streets) for(const h of s.hs){ const d=distM(lat,lon,h[1],h[2]); if(d<60&&(!best||d<best.d)) best={d,n:`${s.n} ${h[0]}`}; }
  if(best) return best.n;
  for(const s of PLACES.list){ if(s.t!=='street') continue; const d=distM(lat,lon,s.lat,s.lon); if(d<250&&(!best||d<best.d)) best={d,n:s.name}; }
  return best?best.n:T('pointMap');
}

/* ---------- the search ---------- */
let XF=null;
function stopGrid(){ if(stopGrid.g) return stopGrid.g; const g=new Map();
  S.forEach(s=>{ if(!stopRefs[s.i].length) return; const k=Math.floor(s.lat*200)+','+Math.floor(s.lon*150); if(!g.has(k)) g.set(k,[]); g.get(k).push(s); });
  return stopGrid.g=g; }
function nearStops(lat,lon,max){ const g=stopGrid(), a=Math.floor(lat*200), b=Math.floor(lon*150), r=Math.ceil(max/550), out=[];
  for(let i=-r;i<=r;i++) for(let j=-r;j<=r;j++) for(const s of g.get((a+i)+','+(b+j))||[]){ const d=distM(lat,lon,s.lat,s.lon); if(d<=max) out.push([s.i,d]); }
  return out.sort((x,y)=>x[1]-y[1]); }
function footpaths(){ if(XF) return XF; XF=S.map(s=>stopRefs[s.i].length?nearStops(s.lat,s.lon,XFER_M).filter(x=>x[0]!==s.i):[]); return XF; }
/* live arrival times turned into a delay per trip: each live arrival at a stop is matched to the trip of
   that line whose timetable time there is closest (within 15 minutes) */
function liveDelays(c){
  const del=new Map(); if(!c.live) return del;
  for(const [s,e] of liveStops){ if(!fresh(e)) continue; const el=(Date.now()-e.at)/1000;
    for(const x of e.rows){ const at=c.nowSec+x.sec-el; let best=null;
      for(const {p,k} of stopRefs[s]){ if(p.r!==x.r) continue;
        for(const t of tripsOn(p,c.key)){ const sch=t.start+t.prof[k], d=at-sch; if(Math.abs(d)<=900&&(!best||Math.abs(d)<Math.abs(best.d))) best={key:p.id+'|'+t.start,d}; } }
      if(best&&!del.has(best.key)) del.set(best.key,best.d); } }
  return del;
}
function raptor(o,dst,dep,c,del,maxWalk){
  const N=S.length, INF=1e15, best=new Float64Array(N).fill(INF), rounds=[], key=c.key;
  const arr0=new Float64Array(N).fill(INF), par0=new Array(N), marked=new Set();
  const oS=nearStops(o.lat,o.lon,maxWalk), dS=nearStops(dst.lat,dst.lon,maxWalk);
  if(o.stop!=null&&!oS.some(x=>x[0]===o.stop)) oS.unshift([o.stop,0]);
  if(dst.stop!=null&&!dS.some(x=>x[0]===dst.stop)) dS.unshift([dst.stop,0]);
  for(const [s,d] of oS){ const t=dep+walkSec(d); arr0[s]=t; best[s]=t; par0[s]={w:'start',d}; marked.add(s); }
  rounds.push({arr:arr0,par:par0});
  const at=(p,t,i)=>t.start+t.prof[i]+(del.get(p.id+'|'+t.start)||0);
  const out=[];
  let target=INF;
  for(let k=1;k<=MAX_ROUNDS&&marked.size;k++){
    const prev=rounds[k-1].arr, cur=Float64Array.from(prev), cp=new Array(N), qp=new Map();
    for(const s of marked) for(const {p,k:ix} of stopRefs[s]){ const e=qp.get(p); if(e==null||ix<e) qp.set(p,ix); }
    marked.clear(); const improved=new Set();
    for(const [p,from] of qp){
      const trips=tripsOn(p,key); if(!trips.length) continue;
      let trip=null, bi=-1;
      for(let i=from;i<p.s.length;i++){
        const s=p.s[i], closed=stopClosed(s,p.r,key);
        if(trip&&!closed){ const ta=at(p,trip,i); if(ta<best[s]&&ta<target){ cur[s]=ta; best[s]=ta; cp[s]={w:'bus',p,t:trip,bi,ai:i}; improved.add(s); } }
        if(prev[s]<INF&&!closed&&i<p.s.length-1){
          const need=prev[s]+(k>1?XFER_BUF:0);
          if(!trip||at(p,trip,i)>=need){ let cand=null;
            for(const t of trips){ const tt=at(p,t,i); if(tt>=need&&(!cand||tt<at(p,cand,i))) cand=t; if(t.start>need+3600) break; }
            if(cand&&(!trip||at(p,cand,i)<at(p,trip,i))){ trip=cand; bi=i; } }
        }
      }
    }
    for(const s of improved){ marked.add(s);
      for(const [q,d] of footpaths()[s]){ const t=cur[s]+walkSec(d); if(t<cur[q]&&t<best[q]){ cur[q]=t; best[q]=t; cp[q]={w:'walk',from:s,d}; marked.add(q); } } }
    rounds.push({arr:cur,par:cp});
    let bj=null; for(const [s,d] of dS){ if(!cp[s]||cp[s].w!=='bus') continue;  /* walk straight from the stop you get off at */ const t=cur[s]+walkSec(d); if(!bj||t<bj.t) bj={t,s,d}; }
    if(bj){ target=Math.min(target,bj.t); out.push(rebuild(rounds,k,bj,o,dst)); }
  }
  return out;
}
function rebuild(rounds,k,bj,o,dst){
  const legs=[{t:'walk',d:bj.d,to:dst,fromStop:bj.s,end:bj.t}]; let s=bj.s, r=k, guard=0;
  while(guard++<20){
    let pr=rounds[r].par[s]; while(!pr&&r>0){ r--; pr=rounds[r].par[s]; }
    if(!pr||pr.w==='start'){ legs.push({t:'walk',d:pr?pr.d:0,from:o,toStop:s}); break; }
    if(pr.w==='walk'){ legs.push({t:'walk',d:pr.d,fromStop:pr.from,toStop:s}); s=pr.from; continue; }
    legs.push({t:'bus',p:pr.p,trip:pr.t,bi:pr.bi,ai:pr.ai}); s=pr.p.s[pr.bi]; r--;
  }
  return legs.reverse();
}
/* timings, changes and walking of a journey; you leave just in time to catch the first bus */
function finish(legs,del,c){
  const buses=legs.filter(l=>l.t==='bus'); if(!buses.length) return null;
  for(const b of buses){ const dl=del.get(b.p.id+'|'+b.trip.start); b.live=dl!=null;
    b.dep=b.trip.start+b.trip.prof[b.bi]+(dl||0); b.arr=b.trip.start+b.trip.prof[b.ai]+(dl||0); }
  let t=buses[0].dep;
  for(let i=legs.indexOf(buses[0])-1;i>=0;i--){ legs[i].end=t; t-=walkSec(legs[i].d); legs[i].start=t; }
  let prev=null;
  for(const l of legs){ if(l.t==='bus'){ prev=l.arr; continue; } if(prev!=null&&l.start==null){ l.start=prev; l.end=prev+walkSec(l.d); prev=l.end; } }
  const last=legs[legs.length-1];
  return {legs,leave:legs[0].start??buses[0].dep,arrive:last.end??buses[buses.length-1].arr,changes:buses.length-1,
          walkM:legs.filter(l=>l.t==='walk').reduce((a,l)=>a+l.d,0),sig:buses.map(b=>b.p.r+':'+b.p.s[b.bi]+':'+b.trip.start).join('>')};
}
async function runPlan(){
  if(!plan.from||!plan.to){ plan.opts=null; return; }
  const seq=++plan.seq, c=ctx(); plan.busy=true; plan.err=null; render(true);
  const o=plan.from.t==='me'&&meLL?Object.assign({},plan.from,{lat:meLL[0],lon:meLL[1]}):plan.from, dst=plan.to;
  const dep0=plan.leave!=null?plan.leave:(c.live?c.nowSec:6*3600);
  /* fresh live times for the stops near the start (at most 2.5 s) */
  if(c.live){ const near=nearStops(o.lat,o.lon,MAX_WALK).slice(0,6).map(x=>x[0]).filter(s=>!fresh(liveStops.get(s)));
    await Promise.race([Promise.all(near.map(s=>pollStop(s).catch(()=>{}))),new Promise(ok=>setTimeout(ok,2500))]); }
  if(seq!==plan.seq) return;
  const del=liveDelays(c), found=new Map();
  for(const maxWalk of [MAX_WALK,MAX_WALK_FAR]){
    let dep=dep0;
    for(let run=0;run<4;run++){
      const js=raptor(o,dst,dep,c,del,maxWalk).map(l=>finish(l,del,c)).filter(Boolean);
      if(!js.length) break;
      js.forEach(j=>{ if(!found.has(j.sig)) found.set(j.sig,j); });
      dep=Math.min(...js.map(j=>j.legs.find(l=>l.t==='bus').dep))-walkSec(0)+60;
    }
    if(found.size) break;
  }
  const direct=distM(o.lat,o.lon,dst.lat,dst.lon);
  let opts=[...found.values()].filter(j=>j.leave>=dep0-60);
  /* drop options that are worse in every way than another one */
  opts=opts.filter(a=>!opts.some(b=>b!==a&&b.arrive<=a.arrive&&b.changes<=a.changes&&b.walkM<=a.walkM+50&&b.leave>=a.leave&&(b.arrive<a.arrive||b.changes<a.changes||b.leave>a.leave)));
  /* …or that leave about as early as another and get there 10 minutes later */
  opts=opts.filter(a=>!opts.some(b=>b!==a&&b.arrive<=a.arrive-600&&b.leave>=a.leave-300&&b.changes<=a.changes));
  opts.sort((a,b)=>a.arrive-b.arrive||a.changes-b.changes).splice(6);
  if(direct<=2500){ const w=walkSec(direct); opts.push({walk:true,leave:dep0,arrive:dep0+w,changes:0,walkM:direct,legs:[{t:'walk',d:direct,from:o,to:dst,start:dep0,end:dep0+w}]});
    opts.sort((a,b)=>a.arrive-b.arrive); }
  plan.opts=opts; plan.busy=false; plan.sel=0; planDrawn=null;
  if(seq===plan.seq){ render(true); syncMap(); }
}

/* ---------- screens ---------- */
const placeLabel=x=>x?(x.t==='me'?T('myLoc'):x.name):'';
function planView(){
  if(plan.edit) return planEditView();
  const c=ctx();
  let h=`<div class="pad"><div class="pbox"><div class="pfields">
    <button class="pfield ${plan.from?'':'empty'}" data-pfield="from"><span class="pdot o"></span><span>${plan.from?esc(placeLabel(plan.from)):T('fromPh')}</span></button>
    <button class="pfield ${plan.to?'':'empty'}" data-pfield="to"><span class="pdot d"></span><span>${plan.to?esc(placeLabel(plan.to)):T('toPh')}</span></button></div>
    <button class="ibtn pswap" data-pswap aria-label="${T('swap')}"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4v16M3 8l4-4 4 4M17 20V4M21 16l-4 4-4-4"/></svg></button></div>`;
  h+=`<div class="ptime"><button class="chip" data-pleave="now" aria-pressed="${plan.leave==null}">${T(c.live?'leaveNow':'leaveFirst')}</button><label class="chip ptimel" data-on="${plan.leave!=null}">${T('leaveAt')} <input type="time" id="ptime" value="${plan.leave!=null?hhmm(plan.leave):''}"></label></div>`;
  if(!plan.from||!plan.to){ h+=`<p class="note">${T('planHint')}</p>`; return h+'</div>'; }
  if(plan.busy) return h+`<div class="list" aria-busy="true">${[0,1,2].map(()=>`<div class="dep skel" aria-hidden="true"><span class="badge">00</span><div class="main"><i></i><i></i></div><div class="when"><i></i></div></div>`).join('')}</div></div>`;
  if(!plan.opts||!plan.opts.length) return h+`<div class="list"><div class="empty">${T('noTrips')}</div></div></div>`;
  h+=`<div class="eyebrow">${T('options')}</div><div class="list">`;
  plan.opts.forEach((j,i)=>{ const dur=Math.round((j.arrive-j.leave)/60), b0=j.legs.find(l=>l.t==='bus');
    const leaveTxt=c.live&&j.leave-c.nowSec<3600?(j.leave-c.nowSec<60?T('leaveNowShort'):T('leaveIn',{n:Math.round((j.leave-c.nowSec)/60)})):T('leaveT',{time:hhmm(j.leave)});
    h+=`<button class="row popt ${i===plan.sel?'sel':''}" data-popt="${i}"><div class="main"><div class="t1"><b>${hhmm(j.leave)} – ${hhmm(j.arrive)}</b> <span class="pdur">${T('minTotal',{n:dur})}</span></div>
      <div class="psteps">${tripChips(j)}</div>
      <div class="t2">${j.walk?T('walkOnly'):`${esc(leaveTxt)} · ${T(j.changes===0?'direct':j.changes===1?'oneChange':'nChanges',{n:j.changes})} · ${T('walkTotal',{n:walkMin(j.walkM)})}${b0&&b0.live?` · <span class="live"><span class="livedot"></span>${T('live')}</span>`:''}`}</div></div>
      <span class="chev" aria-hidden="true">›</span></button>`; });
  return h+`</div><p class="note">${T('planNote')}</p></div>`;
}
const WALK_ICON='<svg class="walkic" viewBox="0 0 24 24" aria-hidden="true"><circle cx="13" cy="4" r="2" fill="currentColor"/><path d="M10 21l2-6 3 3v5M8 12l3-4 4 2 2 3M11 8l-1 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
function tripChips(j){ return j.legs.filter(l=>l.t==='bus'||l.d>60).map(l=>l.t==='bus'?badge(l.p.r,'sm'):`<span class="pwalk">${WALK_ICON}${walkMin(l.d)}</span>`).join('<span class="parr" aria-hidden="true">›</span>'); }
function planEditView(){
  const which=plan.edit;
  let h=`<div class="pad"><div class="pedit"><button class="back" data-pcancel aria-label="${T('back')}">${BACK}</button><input id="q-plan" class="search" type="search" autocomplete="off" placeholder="${T(which==='from'?'fromPh':'toPh')}" value="${esc(plan.q)}"></div><div id="res">${planSugs()}</div></div>`;
  return h;
}
function planSugs(){
  let h='<div class="list">';
  if(!plan.q.trim()){
    if(navigator.geolocation) h+=`<button class="row" data-psug="me"><span class="pic">📍</span><div class="main"><div class="t1">${T('myLoc')}</div></div></button>`;
    h+=`<button class="row" data-psug="map"><span class="pic">🗺</span><div class="main"><div class="t1">${T('onMap')}</div></div></button>`;
    return h+`</div><p class="note">${T('searchHint')}</p>`;
  }
  const res=planSearch(plan.q); planSugs.last=res;
  if(!PLACES) h+=`<div class="empty">${T('loading')}</div>`;
  else if(!res.length) h+=`<div class="empty">${T('noMatch')}</div>`;
  res.forEach((x,i)=>{ const ic=x.t==='stop'?'🚏':x.t==='addr'?'🏠':x.t==='street'?'🛣':KIND_ICON(x.kind||'');
    const sub=(x.t==='stop'?`${T('kStop')} · ${esc(x.code)}`:x.t==='addr'?T('kAddr'):x.t==='street'?T('kStreet'):T('kPlace'))+(x.area?' · '+esc(x.area):'');
    h+=`<button class="row" data-psug="${i}"><span class="pic" aria-hidden="true">${ic}</span><div class="main"><div class="t1">${esc(x.name)}</div><div class="t2">${sub}</div></div></button>`; });
  return h+'</div>';
}
function tripView(v){
  const j=plan.opts&&plan.opts[v.i]; if(!j) return `<div class="pad"><div class="empty">${T('noTrips')}</div></div>`;
  const c=ctx(), dur=Math.round((j.arrive-j.leave)/60);
  let h=`<div class="dhead"><button class="back" data-back aria-label="${T('back')}">${BACK}</button><div class="main"><h2>${hhmm(j.leave)} – ${hhmm(j.arrive)}</h2><div class="sub">${T('minTotal',{n:dur})} · ${j.walk?T('walkOnly'):T(j.changes===0?'direct':j.changes===1?'oneChange':'nChanges',{n:j.changes})}</div></div></div><div class="pad"><ol class="ptrip">`;
  const nm=x=>x.toStop!=null?S[x.toStop].name:x.to?placeLabel(x.to):'';
  j.legs.forEach((l,i)=>{
    if(l.t==='walk'){ if(l.d<15&&!(i===0||i===j.legs.length-1)) return;
      const dest=l.toStop!=null?S[l.toStop].name:placeLabel(l.to);
      h+=`<li class="pw"><span class="ptime-l">${l.start!=null?hhmm(l.start):''}</span><div><b>${i===0&&l.from?esc(placeLabel(l.from)):''}</b><div class="t2">${WALK_ICON} ${T('walkTo',{n:walkMin(l.d),m:Math.round(l.d*DETOUR/10)*10,place:esc(dest)})}</div></div></li>`; }
    else { const x=R[l.p.r], n=l.ai-l.bi, wait=c.live?l.dep-c.nowSec:null;
      h+=`<li class="pb" style="--lc:${x.col}"><span class="ptime-l">${hhmm(l.dep)}</span><div><b>${esc(S[l.p.s[l.bi]].name)}</b>
        <button class="ptake" data-line="${l.p.r}" data-dirp="${l.p.id}" data-hl="${l.p.s[l.bi]}">${badge(l.p.r)} <span>${T('towards',{dest:esc(l.p.h)})}</span></button>
        <div class="t2">${T('nStopsRide',{n,min:Math.round((l.arr-l.dep)/60)})}${l.live?` · <span class="live"><span class="livedot"></span>${T('live')}</span>`:wait!=null&&wait>0&&wait<3600?` · ${T('inMin',{n:Math.round(wait/60)})}`:''}</div></div></li>
        <li class="pa" style="--lc:${x.col}"><span class="ptime-l">${hhmm(l.arr)}</span><div><b>${esc(S[l.p.s[l.ai]].name)}</b><div class="t2">${T('getOff')}</div></div></li>`; }
  });
  h+=`<li class="pend"><span class="ptime-l">${hhmm(j.arrive)}</span><div><b>${esc(placeLabel(plan.to))}</b></div></li></ol>`;
  return h+`<p class="note">${T('planNote')}</p></div>`;
}

/* ---------- on the map ---------- */
let planLayer=null, planDrawn=null;
/* the trip on the map: while the planner (or one of its trips) is open, not over a line or stop opened from it */
const planMapOn=()=>{ const top=st.stack[st.stack.length-1]; return planOn()&&(!top||top.kind==='trip'); };
function planShown(){ if(!planMapOn()||!plan.opts||!plan.opts.length) return null; const top=st.stack[st.stack.length-1]; return plan.opts[top&&top.kind==='trip'?top.i:plan.sel]||null; }
function slice(p,bi,ai){ const {pts,cum}=SH[p.sh], d0=p.dist[bi], d1=p.dist[ai], out=[pointAlong(p.sh,d0)];
  for(let i=0;i<pts.length;i++) if(cum[i]>d0&&cum[i]<d1) out.push(pts[i]); out.push(pointAlong(p.sh,d1)); return out; }
function drawPlan(){
  if(!map) return; const j=planShown(), sig=j?(j.sig||'walk')+'|'+j.leave:null;
  if(planLayer){ planLayer.remove(); planLayer=null; }
  const pin=(x,cls)=>x&&L.marker([x.t==='me'&&meLL?meLL[0]:x.lat,x.t==='me'&&meLL?meLL[1]:x.lon],{icon:L.divIcon({className:'ppin '+cls,iconSize:[22,22],iconAnchor:[11,11]}),interactive:false,keyboard:false});
  /* its own layer above the bus lines, so a redraw of the network never covers the trip */
  if(!map.getPane('plan')){ const pn=map.createPane('plan'); pn.style.zIndex=450; pn.style.pointerEvents='none'; }
  planLayer=L.layerGroup();
  if(!j) planDrawn=null;          /* coming back to it fits the map to it again */
  if(planMapOn()){ const a=pin(plan.from,'o'), b=pin(plan.to,'d'); if(a) a.addTo(planLayer); if(b) b.addTo(planLayer); }
  if(j){ const all=[];
    for(const l of j.legs){
      if(l.t==='bus'){ const pts=slice(l.p,l.bi,l.ai); all.push(...pts);
        L.polyline(pts,{pane:'plan',color:'#fff',weight:10,opacity:.9,interactive:false}).addTo(planLayer);
        L.polyline(pts,{pane:'plan',color:R[l.p.r].col,weight:6,opacity:1,interactive:false}).addTo(planLayer);
        for(const k of [l.bi,l.ai]){ const s=S[l.p.s[k]]; L.circleMarker([s.lat,s.lon],{pane:'plan',radius:6,color:R[l.p.r].col,weight:3,fillColor:'#fff',fillOpacity:1,interactive:false}).addTo(planLayer); } }
      else { const pa=l.from?[l.from.t==='me'&&meLL?meLL[0]:l.from.lat,l.from.t==='me'&&meLL?meLL[1]:l.from.lon]:[S[l.fromStop].lat,S[l.fromStop].lon];
        const pb=l.to?[l.to.lat,l.to.lon]:[S[l.toStop].lat,S[l.toStop].lon]; all.push(pa,pb);
        L.polyline([pa,pb],{pane:'plan',color:isDarkNow()?'#d1d1d6':'#3a3a3c',weight:4,dashArray:'1 8',lineCap:'round',opacity:.95,interactive:false}).addTo(planLayer); } }
    if(sig!==planDrawn&&all.length){ planDrawn=sig; map.fitBounds(L.latLngBounds(all),fitPad()); }
  }
  planLayer.addTo(map);
}
function setPlace(which,x){
  plan[which]=x; plan.edit=null; plan.q=''; plan.opts=null;
  if(plan.from&&plan.to) runPlan(); else render();
  syncMap();
}
