const test = require('node:test');
const assert = require('node:assert/strict');
require('../ui/orbit-data.js');
const {changes, fillMove} = globalThis.orbitData;
const p = {id:'A', name:'Same name', staked_lots:0, staked:'0.000', cash:'100.000'};
const fill = {n:1, time:7, auction:'3', quantity:3, total:'3.834', buyer_id:'A', seller_id:'B', buyer:'Same name', seller:'Same name'};
const book = {players:[p, {...p,id:'B'}], fill_count:0, recent_fills:[], as_of:4};
test('one peer payment, distinct identities, and exact cash reconciliation with stake and claim', () => {
  const next = {...book, fill_count:1, recent_fills:[fill], players:[{...p,cash:'86.169',staked:'10.000'}, {...p,id:'B',cash:'103.834'}]};
  const events = changes(book,next).events;
  assert.deepEqual(events.map(e=>[e.title,e.amount,e.path]), [
    ['Auction #3','$3.834',['A','@auction','B']], ['Net staked','$10.000',['A','@staking']],
    ['Rewards claimed','$0.003',['@staking','A']]]);
  assert.deepEqual(changes(next,next).events,[]);
  assert.deepEqual(fillMove({...fill,buyer_id:''}).path,['@auction','B']);
  assert.deepEqual(fillMove({...fill,seller_id:''}).path,['A','@auction']);
});
test('missing auction history never produces a spurious cash claim', () => {
  const diff=changes(book,{...book,fill_count:45,players:[{...p,cash:'109.000'}]});
  assert.equal(diff.missed,true); assert.deepEqual(diff.events,[]);
});
test('entries, LOT, and released stable principal keep their own units', () => {
  const old = {...book,players:[{...p,cash:'93.000',staked:'7.000',staked_lots:2}]};
  const next = {...book,players:[p,{...p,id:'C',cash:'95.000',staked:'5.000'}]};
  assert.deepEqual(changes(old,next).events.map(e=>[e.title,e.amount]),[
    ['Net unstaked','$7.000'],['Net unstaked','2 LOT'],['Round entry','$100.000'],['Net staked','$5.000']]);
});
