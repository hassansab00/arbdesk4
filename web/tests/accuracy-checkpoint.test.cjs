// Exercise the actual dropdown callbacks and query selection with a parent-owned checkpoint.
const assert=require('node:assert/strict');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const Module=require('module'),path=require('node:path');
const {selectedFrozenCall}=require('../.route-test/lib/frozenCalls.js');
let states=[],cursor=0,effects=[],pending=[],dirty=false,queries=[];
const hooks={...React,
  useState(initial){
    const i=cursor++;
    if(!(i in states)) states[i]=initial;
    return [states[i],value=>{if(states[i]!==value){states[i]=value;dirty=true;}}];
  },
  useEffect(fn,deps){
    const i=cursor++;
    if(!effects[i] || deps.some((v,j)=>v!==effects[i][j])){
      effects[i]=deps; pending.push(fn);
    }
  }
};
const oldLoad=Module._load,oldResolve=Module._resolveFilename;
Module._resolveFilename=function(name,...args){
  if(name.startsWith('@/')) name=path.join(__dirname,'../.route-test',name.slice(2));
  return oldResolve.call(this,name,...args);
};
Module._load=function(name,...args){
  if(name==='react') return hooks;
  if(name==='@/lib/useQuery') return {useQuery(fn,deps){
    // Dependencies are what the real hook uses to refresh this read.
    queries.push({deps,checkpoint:fn().checkpoint});
    return {data:[],loading:false,error:null,truncated:false,refresh(){}};
  }};
  if(name==='@/lib/supabase') return {supabase:{from(){return {
    select(){return this;},eq(key,value){if(key==='checkpoint') this.checkpoint=value;return this;},in(){return this;},limit(){return this;}
  };}}};
  if(name==='@/components/DataState') return {DataState:()=>null};
  return oldLoad.call(this,name,...args);
};
const Accuracy=require('../.route-test/components/CityPredictionValidation.js').default;
Module._load=oldLoad;
let moment='d1_eve',parentChanges=[];
const setMoment=next=>{parentChanges.push(next);moment=next;};
function render(){
  let tree;
  do {
    dirty=false;cursor=0;pending=[];
    tree=Accuracy({cities:['fixture'],moment,setMoment});
    pending.forEach(fn=>fn());
  }while(dirty);
  return tree;
}
function find(node,label){
  if(!node || typeof node!=='object') return;
  if(node.props?.['aria-label']===label) return node;
  for(const child of React.Children.toArray(node.props?.children)){
    const found=find(child,label);if(found) return found;
  }
}
const calls=['d1_eve','morning','noon'].map(checkpoint=>({city_key:'fixture',target_date:'2001-06-02',checkpoint,
  checkpoint_id:checkpoint,top_label:`Original ${checkpoint}`,top_prob:.5,decided_at:'2001-06-01T18:37:00Z'}));
let tree=render();
assert.equal(find(tree,'Accuracy horizon').props.value,'d1_eve');
find(tree,'Accuracy horizon').props.onChange({target:{value:'morning'}});
tree=render();
assert.equal(moment,'morning','dropdown updates parent, used by cards and hindsight');
assert.equal(queries.at(-1).deps[0],moment,'refresh follows the parent checkpoint');
assert.equal(queries.at(-1).checkpoint,moment,'accuracy queries the same parent checkpoint');
assert.equal(selectedFrozenCall(calls,'fixture','2001-06-02',moment).top_label,'Original morning');
find(tree,'Accuracy horizon').props.onChange({target:{value:'overall'}});
tree=render();
assert.equal(queries.at(-1).checkpoint,'overall','aggregate accuracy remains available');
assert.equal(moment,'morning','aggregate never becomes an invalid frozen-card checkpoint');
assert.equal(selectedFrozenCall(calls,'fixture','2001-06-02',moment).top_label,'Original morning');
assert.match(renderToStaticMarkup(tree),/Overall combines checkpoints/);
assert(!parentChanges.includes('overall'));
moment='noon'; // A change from the main checkpoint controls also leaves aggregate mode.
tree=render();
assert.equal(find(tree,'Accuracy horizon').props.value,'noon');
assert.equal(queries.at(-1).checkpoint,'noon');
assert.equal(selectedFrozenCall(calls,'fixture','2001-06-02',moment).top_label,'Original noon');
find(tree,'Accuracy horizon').props.onChange({target:{value:'day_ahead'}});
tree=render();
assert.equal(moment,'day_ahead');
assert.equal(queries.at(-1).checkpoint,'day_ahead');
console.log('PASS: accuracy controls share frozen-card checkpoint; overall stays an explicit aggregate');
