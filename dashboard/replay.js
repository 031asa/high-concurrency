/* Independent read-only replay panels. No market-service lifecycle actions. */
(() => {
  'use strict';
  const SPEEDS = [1, 10, 60, 300, 600, 1800, 3600];
  const fmt = (v, digits=4) => v == null || !Number.isFinite(v) ? '—' :
    v.toLocaleString('zh-CN', {minimumFractionDigits:digits, maximumFractionDigits:digits});
  const nsDate = t => new Date(Number(t / 1000000n)).toLocaleString('zh-CN',
    {timeZone:'Asia/Shanghai', hour12:false});

  class ReplayClock {
    constructor() { this.speed=10; this.reset(0n,0n); }
    reset(start,end) { this.start=start; this.end=end; this.time=start; this.playing=false; this.stamp=0; }
    advance(now) {
      if (this.playing) {
        const elapsed=Math.max(0,now-this.stamp);
        this.time=this.time+BigInt(Math.round(elapsed*this.speed*1000000));
        if(this.time>this.end)this.time=this.end;
        this.stamp=now;
      }
      return this.time;
    }
    play(now) { this.playing=true; this.stamp=now; }
    pause(now) { this.advance(now); this.playing=false; }
    setSpeed(speed,now) {
      if(!SPEEDS.includes(speed))throw new Error('不支持的倍速');
      this.advance(now); this.speed=speed; this.stamp=now;
    }
    seek(time,now) {
      this.playing=false;
      this.time=time<this.start?this.start:time>this.end?this.end:time;
      this.stamp=now;
    }
  }


  const pauseDelay=(ms,signal)=>new Promise((resolve,reject)=>{
    const abort=()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));};
    const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve();},ms);
    if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});
  });
  async function mapLimited(values,limit,work) {
    let next=0;
    await Promise.all(Array.from({length:Math.min(values.length,limit)},async()=>{
      while(next<values.length){const i=next++;await work(values[i]);}
    }));
  }

  class ReplayCard {
    constructor(workspace,contract) {
      this.workspace=workspace;this.contract=contract;this.points=[];this.loaded=false;this.curveError='';
      this.node=workspace.document.getElementById('replay-template').content.firstElementChild.cloneNode(true);
      this.node.setAttribute('aria-label','合约 '+contract);
      this.el={};
      for(const e of this.node.querySelectorAll('[data-role]'))this.el[e.dataset.role]=e;
      this.el.title.textContent=contract;workspace.container.append(this.node);
      this.tag=workspace.document.createElement('button');
      this.tag.textContent=contract+' ×';this.tag.setAttribute('aria-label','移除 '+contract);
      this.tag.onclick=()=>workspace.edit(workspace.names().filter(c=>c!==contract));
      workspace.el.tags.append(this.tag);
    }
    remove(){this.node.remove();this.tag.remove();}
    render(item,frame) {
      const q=item.quote;
      const labels={before_start:'尚无行情',ended:'已结束，保留最后行情',active:'当前时刻最近一条',
        unavailable:'当前快照中无此合约',error:'读取失败：'+(item.error||'无可用记录')};
      this.el.state.textContent=(labels[item.state]||'等待行情')+(this.curveError?' · 曲线读取失败：'+this.curveError:'');
      this.el.price.textContent=q?fmt(q.price):'—';
      this.el.times.textContent=q?'行情时间：'+q.market_time+' · 原始接收：'+q.receive_time:'尚无可显示的行情';
      this.el.depth.replaceChildren();
      for(let i=0;i<5;i++){
        const row=this.workspace.document.createElement('tr');
        [i+1,...(q?.depth[i]||[null,null,null,null])].forEach((v,j)=>{
          const cell=this.workspace.document.createElement('td');
          cell.textContent=j===0?String(v):fmt(v,j%2?4:0);row.append(cell);
        });this.el.depth.append(row);
      }
      this.el.details.textContent=JSON.stringify({date:frame.date,source:frame.source,time:frame.time,
        loaded_at:frame.loaded_at,generation:frame.generation,incomplete:frame.incomplete,
        warnings:frame.warnings,state:item.state,error:item.error,record:q},null,2);
      this.draw(q);
    }
    draw(q) {
      const svg=this.el.chart;svg.replaceChildren();if(!q)return;
      const valid=this.points.filter(p=>BigInt(p[0])<BigInt(q.received)&&Number.isFinite(p[1]));
      if(Number.isFinite(q.price))valid.push([q.received,q.price]);
      if(!valid.length)return;
      const prices=valid.map(p=>p[1]),lo=Math.min(...prices),hi=Math.max(...prices),span=Math.max(hi-lo,.0001);
      const doc=this.workspace.document,ns='http://www.w3.org/2000/svg',clock=this.workspace.clock;
      const text=(value,x,y,anchor='start')=>{
        const e=doc.createElementNS(ns,'text');e.setAttribute('class','axis-label');e.setAttribute('x',String(x));
        e.setAttribute('y',String(y));e.setAttribute('text-anchor',anchor);e.textContent=value;svg.append(e);
      };
      text(fmt(hi),0,25);text(fmt(lo),0,180);
      const line=doc.createElementNS(ns,'polyline');line.setAttribute('class','chart-line');
      line.setAttribute('points',valid.map(p=>(65+Number(BigInt(p[0])-clock.start)/Math.max(1,Number(clock.end-clock.start))*810)+','+(180-(p[1]-lo)/span*155)).join(' '));
      svg.append(line);text(nsDate(clock.start),65,210);text(nsDate(clock.end),875,210,'end');
    }
  }

  class ReplayWorkspace {
    constructor(document) {
      this.document=document;this.container=document.getElementById('replay-panels');this.el={};
      for(const name of ['date','source','search','contract','tags','all','play','speed','seek','time','state','refresh'])
        this.el[name]=document.getElementById('replay-'+name);
      this.cards=new Map();this.clock=new ReplayClock();this.selection={date:'',source:''};
      this.catalog=null;this.active=false;this.loading=false;this.busy=false;this.refreshing=false;
      this.token=0;this.controllers=new Set();this.lastRequest=-Infinity;this.needsSnapshot=false;
      this.needsReload=true;this.initial=true;this.frame=null;this.notice='';this.indexChanged=false;
      this.el.date.onchange=()=>{this.selection.date=this.el.date.value;this.reload({reset:true});};
      this.el.source.onchange=()=>{this.selection.source=this.el.source.value;this.reload({reset:true});};
      this.el.search.oninput=()=>this.contractOptions();
      this.el.contract.onchange=()=>{
        if(this.el.contract.value)this.edit([...this.names(),this.el.contract.value]);
        this.el.contract.value='';
      };
      this.el.all.onclick=()=>this.edit([...this.names(),...(this.catalog?.contracts||[])]);
      this.el.refresh.onclick=()=>this.reload({refresh:true});
      this.el.speed.onchange=()=>{
        this.cancel();this.clock.setSpeed(Number(this.el.speed.value),performance.now());this.needsSnapshot=true;
      };
      this.el.play.onclick=()=>{
        if(!this.cards.size||this.loading||this.indexChanged)return;
        this.cancel();
        if(this.clock.playing){this.clock.pause(performance.now());this.status('已暂停');}
        else {
          if(this.clock.time>=this.clock.end){this.clock.seek(this.clock.start,performance.now());this.frame=null;}
          this.clock.play(performance.now());this.status('同步播放中');
        }
        this.needsSnapshot=true;this.controls();
      };
      this.el.seek.oninput=()=>{
        this.interrupt();
        this.clock.seek(this.clock.start+(this.clock.end-this.clock.start)*BigInt(this.el.seek.value)/10000n,performance.now());
        this.frame=null;this.needsSnapshot=true;this.status('已暂停');this.controls();
      };
      this.el.seek.onchange=()=>this.tick(performance.now());
      this.timer=setInterval(()=>this.tick(performance.now()),250);
      this.controls();
    }
    names(){return Array.from(this.cards.keys());}
    current(token){return this.active&&token===this.token;}
    cancel(){this.token++;for(const c of this.controllers)c.abort();this.controllers.clear();}
    interrupt(){this.clock.pause(performance.now());this.cancel();}
    async request(path,token,body) {
      const controller=new AbortController();this.controllers.add(controller);
      try {
        if(!this.current(token))throw new DOMException('Aborted','AbortError');
        const response=await fetch(path,{cache:'no-store',signal:controller.signal,
          method:body===undefined?'GET':'POST',...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
        if(!response.ok)throw new Error('接口 HTTP '+response.status);
        return await response.json();
      } finally {this.controllers.delete(controller);}
    }
    async wait(token) {
      const c=new AbortController();this.controllers.add(c);
      try {
        if(!this.current(token))throw new DOMException('Aborted','AbortError');
        await pauseDelay(1000,c.signal);
      } finally {this.controllers.delete(c);}
    }
    status(message) {
      this.el.state.textContent=message+(this.notice?' · '+this.notice:'')+
        (this.catalog?.loaded_at?' · 截至加载时刻 '+this.catalog.loaded_at:'')+
        (this.catalog?.incomplete?' · 归档不完整，详见各合约数据详情':'');
    }
    controls() {
      for(const name of ['date','source','search','contract','all'])this.el[name].disabled=this.loading||!this.catalog;
      this.el.all.disabled=this.loading||!this.catalog?.contracts?.some(c=>!this.cards.has(c));
      this.el.play.disabled=this.loading||!this.cards.size||this.indexChanged;
      this.el.seek.disabled=this.el.play.disabled;this.el.speed.disabled=this.loading;
      this.el.refresh.disabled=this.refreshing;
      this.el.play.textContent=this.clock.playing?'暂停':'播放';
    }
    options(name,values,selected) {
      const select=this.el[name];select.replaceChildren();
      for(const value of values){
        const o=this.document.createElement('option');o.value=value;
        o.textContent=name==='source'?value.toUpperCase():value;select.append(o);
      }
      if(selected&&!values.includes(selected)){
        const o=this.document.createElement('option');o.value=selected;o.textContent=selected+'（不可用）';select.append(o);
      }
      select.value=selected||'';
    }
    contractOptions() {
      const select=this.el.contract;select.replaceChildren();
      const placeholder=this.document.createElement('option');placeholder.value='';placeholder.textContent='选择合约即添加';select.append(placeholder);
      const search=this.el.search.value.trim().toLowerCase();
      for(const c of this.catalog?.contracts||[]){
        if(this.cards.has(c)||!c.toLowerCase().includes(search))continue;
        const o=this.document.createElement('option');o.value=c;o.textContent=c;select.append(o);
      }
      select.value='';
    }
    async reload({reset=false,refresh=false}={}) {
      this.interrupt();const token=this.token;this.loading=true;this.refreshing=refresh;
      this.needsReload=true;this.notice='';this.status(refresh?'刷新中，所有合约已暂停':'数据加载中');this.controls();
      try {
        if(refresh)await this.request('/api/replay/refresh',token,{});
        let data;
        do {
          data=await this.request('/api/replay/catalog?'+new URLSearchParams(this.selection),token);
          if(!this.current(token))return;
          if(data.status==='loading'||data.status==='idle')await this.wait(token);
        } while(data.status==='loading'||data.status==='idle');
        if(data.status!=='ready')throw new Error(data.error||'索引不可用');
        this.selection={date:this.selection.date||data.date,source:this.selection.source||data.source};
        this.options('date',data.dates||[],this.selection.date);this.options('source',data.sources||[],this.selection.source);
        const names=this.initial?(data.contracts||[]).slice(0,1):this.names();
        const dropped=names.filter(c=>!data.contracts.includes(c));
        const changed=!this.catalog||this.catalog.generation!==data.generation||
          this.catalog.date!==data.date||this.catalog.source!==data.source;
        this.catalog=data;this.initial=false;this.indexChanged=false;this.needsReload=false;
        if(changed)for(const card of this.cards.values()){card.loaded=false;card.points=[];card.curveError='';}
        this.notice=dropped.length?'已移除不可用合约：'+dropped.join('、'):'';
        this.refreshing=false;
        await this.edit(names.filter(c=>data.contracts.includes(c)),{reset,keepNotice:true});
      } catch(error) {
        if(this.current(token)&&error.name!=='AbortError'){
          this.indexChanged=true;this.status('加载失败：'+error.message);
        }
      } finally {
        if(token===this.token){this.loading=false;this.refreshing=false;this.controls();}
      }
    }
    async edit(names,{reset=false,keepNotice=false}={}) {
      if(!this.catalog)return;
      this.interrupt();const token=this.token;const hadCards=this.cards.size>0;
      const saved=this.clock.time;this.frame=null;this.indexChanged=false;
      if(!keepNotice)this.notice='';
      names=Array.from(new Set(names)).filter(c=>this.catalog.contracts.includes(c));
      for(const [c,card] of this.cards)if(!names.includes(c)){card.remove();this.cards.delete(c);}
      for(const c of names)if(!this.cards.has(c))this.cards.set(c,new ReplayCard(this,c));
      this.contractOptions();this.loading=true;this.needsSnapshot=false;
      if(!this.cards.size){
        this.clock.reset(0n,0n);this.loading=false;this.el.time.textContent='—';this.el.seek.value='0';
        this.status('请选择合约');this.controls();return;
      }
      const bounds=names.map(c=>this.catalog.contract_bounds[c]);
      const start=bounds.reduce((n,b)=>BigInt(b.start)<n?BigInt(b.start):n,BigInt(bounds[0].start));
      const end=bounds.reduce((n,b)=>BigInt(b.end)>n?BigInt(b.end):n,0n);
      this.clock.reset(start,end);
      if(!reset&&hadCards){
        this.clock.seek(saved,performance.now());
        if(saved!==this.clock.time)this.notice+=(this.notice?'；':'')+'当前位置超出新范围，已定位到最近边界';
      }
      this.status('曲线加载中 · 已选 '+this.cards.size+' 个合约');this.controls();
      try {
        const cards=Array.from(this.cards.values()).filter(c=>!c.loaded);
        await mapLimited(cards,4,async card=>{
          if(!this.current(token))return;
          const params=new URLSearchParams({...this.selection,contract:card.contract,time:this.catalog.contract_bounds[card.contract].end});
          try {
            const result=await this.request('/api/replay/curve?'+params,token);
            if(!this.current(token))return;
            if(result.status!=='ready'||result.generation!==this.catalog.generation){
              this.indexChanged=true;throw new Error('索引已变化，请刷新归档快照');
            }
            card.points=result.points||[];card.loaded=true;card.curveError='';
          } catch(error) {
            if(this.current(token)&&error.name!=='AbortError')card.curveError=error.message;
          }
        });
        if(!this.current(token))return;
        if(this.indexChanged){this.status('索引已变化，请刷新归档快照');return;}
        this.needsSnapshot=true;this.status('已暂停 · 已选 '+this.cards.size+' 个合约');
      } finally {
        if(this.current(token)){this.loading=false;this.controls();this.tick(performance.now());}
      }
    }
    render(data,target) {
      const items=new Map(data.items.map(item=>[item.contract,item]));
      for(const [c,card] of this.cards)card.render(items.get(c)||{state:'error',error:'响应缺少合约',quote:null},data);
      this.frame=data;this.el.time.textContent=nsDate(target);
      this.el.seek.value=String(Number((target-this.clock.start)*10000n/((this.clock.end-this.clock.start)||1n)));
    }
    async tick(now) {
      if(!this.active||this.loading||this.refreshing||this.indexChanged||!this.cards.size||!this.catalog)return;
      let target=this.clock.advance(now);
      if(this.clock.playing&&this.frame&&!this.frame.items.some(i=>i.state==='error'||i.state==='unavailable')){
        const next=this.frame.items.filter(i=>i.next).map(i=>BigInt(i.next));
        if(next.length){
          const earliest=next.reduce((a,b)=>a<b?a:b);
          if(earliest-target>60000000000n){
            this.clock.time=target=earliest;this.status('跳过所有已选合约均无记录的区间（不判定休市或断线）');
          }
        }
      }
      if(this.busy||now-this.lastRequest<250||(!this.clock.playing&&!this.needsSnapshot))return;
      this.busy=true;this.lastRequest=now;this.needsSnapshot=false;const token=this.token;
      try {
        const data=await this.request('/api/replay/snapshots',token,{...this.selection,contracts:this.names(),time:String(target)});
        if(!this.current(token))return;
        if(data.status!=='ready'||data.generation!==this.catalog.generation){
          this.indexChanged=true;throw new Error('索引已变化，请刷新归档快照');
        }
        if(data.time!==String(target)||data.date!==this.selection.date||data.source!==this.selection.source)
          throw new Error('快照选择或时间不一致');
        this.render(data,target);
        if(target>=this.clock.end&&this.clock.playing){
          this.clock.seek(this.clock.end,performance.now());this.status('已结束');this.controls();
        }
      } catch(error) {
        if(this.current(token)&&error.name!=='AbortError'){
          this.clock.pause(performance.now());this.status('读取失败：'+error.message);this.controls();
        }
      } finally {this.busy=false;}
    }
    enter(){
      this.active=true;
      if(this.needsReload)this.reload();
      else {this.needsSnapshot=!!this.cards.size;this.controls();this.tick(performance.now());}
    }
    leave(){
      if(this.loading)this.needsReload=true;
      this.interrupt();this.active=false;this.loading=false;this.refreshing=false;
      this.needsSnapshot=!!this.cards.size;this.status('已暂停');this.controls();
    }
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={ReplayClock,ReplayCard,ReplayWorkspace,SPEEDS,mapLimited};
  else window.ReplayWorkspace=ReplayWorkspace;
})();
