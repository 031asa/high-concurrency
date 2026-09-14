const {test,afterEach}=require('node:test');
const assert=require('node:assert/strict');
const {ReplayClock,ReplayWorkspace,SPEEDS}=require('../dashboard/replay.js');
let now=0;
Object.defineProperty(globalThis,'performance',{value:{now:()=>now},configurable:true});
const ns=s=>BigInt(s)*1000000000n;
class Element{
  constructor(role=''){this.dataset={role};this.children=[];this.value='';this.textContent='';}
  append(e){e.parent=this;this.children.push(e);}
  replaceChildren(){this.children=[];}
  setAttribute(k,v){this[k]=v;}
  get options(){return this.children;}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
}
const roles=['title','state','price','times','chart','depth','details'];
function document(){
  const e={};
  for(const id of ['panels','date','source','search','contract','tags','all','play','speed','seek','time','state','refresh'])e['replay-'+id]=new Element();
  e['replay-template']={content:{firstElementChild:{cloneNode(){
    const n=new Element();n.fields=roles.map(r=>new Element(r));n.querySelectorAll=()=>n.fields;return n;
  }}}};
  return {getElementById:id=>e[id],createElement:()=>new Element(),createElementNS:()=>new Element()};
}
const spaces=[];
function catalog(names=['A','B']){
  return {status:'ready',generation:'g',date:'2026-09-11',source:'live',dates:['2026-09-11','2026-09-10'],
    sources:['live','tts'],contracts:names,contract_bounds:Object.fromEntries(names.map(c=>[c,{count:2,start:String(ns(1)),end:String(ns(7201))}]))};
}
function workspace(names=['A','B']){
  const w=new ReplayWorkspace(document());clearInterval(w.timer);spaces.push(w);
  w.active=true;w.catalog=catalog(names);w.selection={date:'2026-09-11',source:'live'};
  w.initial=false;w.needsReload=false;return w;
}
const reply=data=>({ok:true,json:async()=>data});
function frame(body){
  return {status:'ready',generation:'g',...body,items:body.contracts.map((c,i)=>({contract:c,state:'active',next:null,
    quote:{price:100+i,depth:[[99+i,4,101+i,7]],received:body.time,receive_time:body.time,market_time:'09:00:00'}}))};
}
function api(w){
  global.fetch=async(url,opts)=>reply(url.includes('/snapshots')?frame(JSON.parse(opts.body)):
    url.includes('/curve')?{status:'ready',generation:'g',points:[]}:w.catalog);
}
const flush=()=>new Promise(r=>setImmediate(r));
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};}
afterEach(()=>{for(const w of spaces.splice(0))w.leave();now=0;});
test('all seven speeds advance known elapsed time; pause, reanchor, seek and end',()=>{
  assert.deepEqual(SPEEDS,[1,10,60,300,600,1800,3600]);
  for(const s of SPEEDS){
    const c=new ReplayClock();c.reset(0n,ns(10000));c.setSpeed(s,0);c.play(0);
    assert.equal(c.advance(1000),ns(s));c.pause(1500);const saved=c.time;
    assert.equal(c.advance(4000),saved);c.setSpeed(1,4000);c.play(4000);
    assert.equal(c.advance(5000),saved+ns(1));c.seek(ns(20000),6000);assert.equal(c.time,ns(10000));
  }
});
test('add, duplicate, all 64, search and tag remove; empty disables play',async()=>{
  const names=Array.from({length:64},(_,i)=>'C'+i),w=workspace(names);api(w);
  await w.edit([names[0],names[0]]);await flush();assert.equal(w.cards.size,1);
  w.el.search.value='C12';w.contractOptions();assert.equal(w.el.contract.options[1].value,'C12');
  await w.edit(names);await flush();assert.equal(w.cards.size,64);assert.equal(w.el.tags.children.length,64);
  w.cards.get('C12').tag.onclick();await flush();assert.equal(w.cards.size,63);
  await w.edit([]);assert.equal(w.el.play.disabled,true);assert.match(w.el.state.textContent,/请选择/);
});
test('editing during play pauses, retains target, and clamps new bounds visibly',async()=>{
  const w=workspace();api(w);await w.edit(['A']);await flush();
  w.clock.play(0);now=1000;await w.edit(['A','B']);await flush();
  assert.equal(w.clock.playing,false);assert.equal(w.clock.time,ns(11));
  w.catalog.contract_bounds.B={count:1,start:String(ns(100)),end:String(ns(200))};
  await w.edit(['B']);assert.equal(w.clock.time,ns(100));assert.match(w.notice,/最近边界/);
});
test('all cards receive same frame and common chart bounds, separate depth and prices',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();
  const [a,b]=Array.from(w.cards.values());
  assert.equal(a.el.price.textContent,'100.0000');assert.equal(b.el.price.textContent,'101.0000');
  assert.equal(a.el.depth.children[0].children[1].textContent,'99.0000');
  assert.equal(b.el.depth.children[0].children[1].textContent,'100.0000');
  assert.equal(a.el.depth.children[4].children[1].textContent,'—');
  assert.equal(a.el.chart.children.at(-1).textContent,b.el.chart.children.at(-1).textContent);
});
test('before first quote is empty; ended contract retains last quote; per-card error isolated',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();
  const f=frame({...w.selection,contracts:w.names(),time:String(ns(5))});
  f.items[0]={contract:'A',state:'before_start',quote:null,next:String(ns(10))};
  f.items[1].state='ended';w.render(f,ns(5));
  assert.equal(w.cards.get('A').el.price.textContent,'—');assert.match(w.cards.get('A').el.state.textContent,/尚无行情/);
  assert.equal(w.cards.get('B').el.price.textContent,'101.0000');assert.match(w.cards.get('B').el.state.textContent,/已结束/);
  f.items[0]={contract:'A',state:'error',error:'corrupt',quote:null};w.render(f,ns(5));
  assert.match(w.cards.get('A').el.state.textContent,/corrupt/);assert.equal(w.cards.get('B').el.price.textContent,'101.0000');
});
test('curve loading never exceeds four requests for 64 contracts',async()=>{
  const names=Array.from({length:64},(_,i)=>'C'+i),w=workspace(names);
  let active=0,max=0;
  global.fetch=async(url,opts)=>{
    if(url.includes('/snapshots'))return reply(frame(JSON.parse(opts.body)));
    active++;max=Math.max(max,active);await flush();active--;
    return reply({status:'ready',generation:'g',points:[]});
  };
  await w.edit(names);await flush();assert.equal(max,4);assert.equal(w.cards.size,64);
});
test('single in-flight batch and maximum 4 requests per second regardless of contracts',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();
  w.clock.play(0);w.lastRequest=-Infinity;
  const d=deferred();let calls=0,payload;
  global.fetch=(url,o)=>{calls++;payload=JSON.parse(o.body);return d.promise;};
  const request=w.tick(1000);await w.tick(1250);await w.tick(1500);assert.equal(calls,1);
  d.resolve(reply(frame(payload)));await request;
  global.fetch=async(url,o)=>{calls++;return reply(frame(JSON.parse(o.body)));};
  await w.tick(1501);await w.tick(1600);await w.tick(1700);assert.equal(calls,2);
  await w.tick(1751);assert.equal(calls,3);
});
test('late response after seek, removal or selection change is discarded',async()=>{
  for(const action of ['seek','remove','selection']){
    const w=workspace();api(w);await w.edit(['A','B']);await flush();w.lastRequest=-Infinity;
    const previous=w.cards.get('A').el.price.textContent,d=deferred();let body;
    global.fetch=(url,o)=>{body=JSON.parse(o.body);return d.promise;};
    w.needsSnapshot=true;const request=w.tick(1000);
    if(action==='seek'){w.el.seek.value='5000';w.el.seek.oninput();}
    else if(action==='remove'){w.interrupt();w.cards.get('B').remove();w.cards.delete('B');}
    else {w.interrupt();w.selection.source='tts';}
    const late=frame(body);late.items[0].quote.price=999;d.resolve(reply(late));await request;
    assert.equal(w.cards.get('A').el.price.textContent,previous);
  }
});
test('skip only common empty gap, never another contract update or unknown error interval',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();w.clock.play(0);w.lastRequest=-Infinity;
  w.frame.items[0].next=String(ns(200));w.frame.items[1].next=String(ns(20));
  await w.tick(250);assert.equal(w.clock.time,ns(1)+2500000000n);
  w.frame.items.forEach(i=>i.next=String(ns(200)));await w.tick(500);
  assert.equal(w.clock.time,ns(200));assert.match(w.el.state.textContent,/所有已选合约/);
  w.frame.items[0].state='error';w.frame.items[1].next=String(ns(500));
  await w.tick(750);assert.equal(w.clock.time,ns(200)+2500000000n);
});
test('3600x reaches exact last timestamp and stops together',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();
  w.clock.setSpeed(3600,0);w.clock.play(0);w.lastRequest=-Infinity;
  await w.tick(2000);assert.equal(w.frame.time,String(ns(7201)));assert.equal(w.clock.playing,false);
  assert.equal(w.el.seek.value,'10000');assert.match(w.el.state.textContent,/已结束/);
});
test('index changes pause comparison, request failures permit retry',async()=>{
  const w=workspace();api(w);await w.edit(['A']);await flush();w.clock.play(0);w.lastRequest=-Infinity;
  global.fetch=async()=>({ok:false,status:500});await w.tick(250);
  assert.equal(w.clock.playing,false);assert.match(w.el.state.textContent,/HTTP 500/);
  w.clock.play(250);global.fetch=async(url,o)=>reply({...frame(JSON.parse(o.body)),generation:'new'});
  await w.tick(500);assert.equal(w.indexChanged,true);assert.equal(w.el.play.disabled,true);
});
test('switch live and back preserves selection/time paused',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();
  w.clock.play(0);now=1000;w.leave();const target=w.clock.time;w.enter();await flush();
  assert.equal(w.clock.time,target);assert.equal(w.clock.playing,false);assert.deepEqual(w.names(),['A','B']);
});
test('date change keeps intersection, reports dropped names, does not substitute',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();
  const next=catalog(['B','C']);next.date='2026-09-10';w.selection.date='2026-09-10';
  global.fetch=async(url,o)=>reply(url.includes('/catalog')?next:url.includes('/curve')?
    {status:'ready',generation:'g',points:[]}:frame(JSON.parse(o.body)));
  await w.reload({reset:true});await flush();assert.deepEqual(w.names(),['B']);assert.match(w.notice,/A/);
  next.contracts=['C'];next.contract_bounds={C:next.contract_bounds.C};await w.reload({reset:true});
  assert.deepEqual(w.names(),[]);assert.equal(w.el.play.disabled,true);
});
test('refresh preserves selection and position while paused',async()=>{
  const w=workspace();api(w);await w.edit(['A','B']);await flush();w.clock.seek(ns(333),0);
  await w.reload({refresh:true});await flush();
  assert.deepEqual(w.names(),['A','B']);assert.equal(w.clock.time,ns(333));assert.equal(w.clock.playing,false);
});
