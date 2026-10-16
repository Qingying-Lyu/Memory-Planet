(function(root) {
  const intervals = [1, 3, 7, 14, 30];
  const baseline = '2026-09-23';
  function fresh() { return {version:1,day:0,events:[],notes:[],session:null,settings:{weeklyGoal:3,paused:false},feedback:[]}; }
  function uid() { return typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36)+Math.random().toString(36).slice(2); }
  function date(day) { const d = new Date(baseline+'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+day); return d.toISOString().slice(0,10); }
  function score(levels,weights,critical) { const raw=Math.round(levels.reduce((sum,l,i)=>sum+l*weights[i],0)); return critical?Math.min(raw,59):raw; }
  function independence(state,target,presentedAt,hint,correction) {
    if(correction||state.events.some(e=>e.type==='attempt'&&e.target===target&&e.day===state.day))return 'correction';
    if(hint||state.events.slice(presentedAt).some(e=>e.target===target&&e.type==='exposure'))return 'hinted';
    return 'independent';
  }
  function derive(state,concepts) {
    const targets=Object.fromEntries(concepts.map(c=>[c.id,{id:c.id,stage:0,due:null,lastExposure:null,evidence:[],level:'未验证',repair:false,pending:false,learned:false,attempts:[]} ]));
    const tiers={},activeDays=new Set();
    for (const e of state.events) {
      const t=targets[e.target]; if(!t) continue;
      if(e.type==='exposure') {
        t.learned=true; t.lastExposure=e.day;
        t.due=t.due===null?e.day+1:Math.max(t.due,e.day+1); continue;
      }
      if(e.type!=='attempt') continue;
      t.attempts.push(e);
      if(e.status==='pending') {t.pending=true;continue;}
      if(e.status==='void') continue;
      const independent=e.independence==='independent';
      const gap=t.lastExposure===null?null:Math.max(0,e.day-t.lastExposure);
      const wasDue=t.due!==null && e.day>=t.due;
      let tier=0;
      if(independent && e.score>=80 && !e.critical) {
        t.evidence.push({day:e.day,gap,question:e.question,attemptId:e.id});
        t.repair=false;
        if(wasDue && gap>=1) t.stage=Math.min(t.stage+1,4);
        t.due=e.day+intervals[t.stage];
        tier=wasDue&&gap>=1?3:2; activeDays.add(e.day);
      } else if(independent && (e.score<60 || e.critical)) {
        t.repair=true; t.stage=0; t.due=e.day+1;
      } else if(independent) {
        t.repair=true; t.due=e.day+intervals[t.stage];
      } else {
        t.due=e.day+1;
        if(e.score>=80 && !e.critical) {tier=1; activeDays.add(e.day);}
      }
      const key=e.day+':'+e.target; tiers[key]=Math.max(tiers[key]||0,tier);
      t.learned=true;t.lastExposure=e.day;
    }
    for (const t of Object.values(targets)) {
      const ev=t.evidence;
      if(ev.length) t.level='初步掌握';
      if(new Set(ev.map(x=>x.day)).size>=2 && new Set(ev.map(x=>x.question)).size>=2 && ev.some(x=>x.gap>=7)) t.level='已巩固';
      if(t.level==='已巩固'&&ev.some(x=>x.gap>=30)) t.level='长期保持有证据';
      t.isDue=t.due!==null && t.due<=state.day;
    }
    return {targets,points:Object.values(tiers).reduce((a,b)=>a+b,0),tiers,activeDays:[...activeDays]};
  }
  function expose(state,target,reason) {state.events.push({id:uid(),type:'exposure',target,reason,day:state.day});}
  function record(state,data) {
    if(state.events.some(e=>e.id===data.id)) return false;
    const event={...data,type:'attempt',day:state.day,status:'valid'};
    state.events.push(event);
    // Feedback is a new exposure even if the assessment is later disputed.
    expose(state,event.target,'反馈解析');return true;
  }
  function queue(state,concepts) {
    if(state.settings.paused)return [];
    return Object.values(derive(state,concepts).targets).filter(t=>t.isDue&&!t.pending).sort((a,b)=>(Number(b.repair)-Number(a.repair))||(a.due-b.due)).slice(0,3).map(t=>t.id);
  }
  const api={fresh,uid,date,score,independence,derive,expose,record,queue,intervals};root.DemoEngine=api;
  if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
