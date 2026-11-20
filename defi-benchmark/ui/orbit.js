const {stake, auction, treasury, fillMove, changes} = globalThis.orbitData;
const get = id => document.getElementById(id);
const canvas = get('orbit-sky'), ctx = canvas.getContext('2d');
const tau = Math.PI * 2;
const ink = {stake:'#71e5ce', auction:'#ffbf79', grant:'#c9baff'};
const palette = ['#a3b5ff','#f1a1cb','#8ae0bf','#ffbd8c','#a2d9f4','#c9a7ff','#ebd288'];
const names = new Map([[stake, 'Staking'], [auction, 'Dutch auctions'], [treasury, 'Round treasury']]);
const media = matchMedia('(prefers-reduced-motion: reduce)');
let board = null, active = false, paused = media.matches, replay = false, selected = null;
let events = [], flights = [], tracks = [], spots = new Map(), colors = new Map();
let last = 0, elapsed = 0, frame = 0, width = 0, height = 0, gap = false;

function element(tag, value) {
  const node = document.createElement(tag);
  node.textContent = value;
  return node;
}
function controls() {
  const status = !board ? 'Awaiting snapshot' : replay ? 'Replaying fills' : board.round === board.current ? 'Live round' : 'Final round';
  get('orbit-status').textContent = status;
  get('orbit-status').dataset.live = String(!!board && board.round === board.current && !replay);
  get('orbit-motion').textContent = paused ? 'Resume motion' : 'Pause motion';
  const button = get('orbit-replay');
  button.disabled = paused || !board?.recent_fills?.length;
  button.textContent = replay ? 'Stop replay' : 'Replay fills';
  button.title = paused ? 'Resume motion to replay settled auction fills' : 'Replay the most recent settled auction fills';
}
function updateAccount() {
  const mind = board?.players.find(p => p.id === selected);
  get('orbit-account').hidden = !mind;
  if (!mind) return;
  get('orbit-mind-name').textContent = mind.name;
  const fields = [['Liquid', '$' + mind.cash], ['Staked', '$' + mind.staked], ['Rewards', '$' + mind.rewards],
    ['Liquid LOT', mind.lots], ['Staked LOT', mind.staked_lots], ['Auction LOT', mind.escrow_lots]];
  get('orbit-holdings').replaceChildren(...fields.map(([label, value]) => {
    const group = document.createElement('div');
    group.append(element('dt', label), element('dd', value));
    return group;
  }));
}
function select(id) {
  selected = id;
  for (const row of get('orbit-rank-rows').rows) {
    row.querySelector('button').setAttribute('aria-pressed', String(row.dataset.id === id));
  }
  updateAccount();
  paint();
}
function rankings() {
  const body = get('orbit-rank-rows');
  const focused = body.contains(document.activeElement) ? document.activeElement.closest('tr')?.dataset.id : null;
  body.replaceChildren(...(board?.players || []).map(p => {
    const row = document.createElement('tr');
    row.dataset.id = p.id;
    const name = document.createElement('td');
    const button = element('button', p.name);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(selected === p.id));
    button.style.setProperty('--mind', colors.get(p.id));
    button.onclick = () => select(p.id);
    name.append(button);
    row.append(element('td', String(p.rank).padStart(2,'0')), name, element('td', p.score));
    return row;
  }));
  if (focused) [...body.rows].find(row => row.dataset.id === focused)?.querySelector('button').focus({preventScroll:true});
  updateAccount();
}
function activity() {
  get('orbit-events').replaceChildren(...events.slice(0, 6).map(event => {
    const row = document.createElement('li');
    row.dataset.kind = event.kind;
    if (event.quantity) row.title = event.quantity + ' settled for ' + event.amount;
    const time = new Date(event.time * 1000).toISOString().slice(11,19);
    row.append(element('time', time), element('b', event.title), element('span', event.detail), element('strong', event.amount));
    return row;
  }));
  get('orbit-clock').textContent = board ? 'Finalized block ' + board.block.toLocaleString('en-US') : '';
  const quiet = get('orbit-quiet');
  quiet.textContent = !board ? 'Waiting for a finalized snapshot.' : gap ? 'Some fills between snapshots are outside the on-chain history.' :
    !board.players.length ? 'Waiting for minds to register.' : !events.length ?
      (board.round === board.current ? 'Watching for finalized transfers.' : 'No recorded transfers available for this round.') : '';
  quiet.hidden = !quiet.textContent;
}
function queue(moves, spacing) {
  let start = Math.max(elapsed, flights.at(-1)?.start + spacing || 0);
  for (const event of moves) { flights.push({event, start}); start += spacing; }
}
function update(next) {
  if (board && next.round === board.round && next.block < board.block) return;
  const fresh = !board || next.round !== board.round;
  if (fresh) {
    reset();
    events = (next.recent_fills || []).map(fillMove);
  } else if (next.hash !== board.hash || next.block !== board.block) {
    const diff = changes(board, next);
    gap ||= diff.missed;
    events = [...diff.events.slice().reverse(), ...events].slice(0, 32);
    if (!paused && !replay) queue(diff.events, .42);
  }
  board = next;
  // Keep identities on their orbital tracks even as the table reorders by score.
  const assigned = new Set(tracks);
  for (const p of next.players.slice().sort((a, b) => a.id.localeCompare(b.id))) {
    if (!assigned.has(p.id)) {
      colors.set(p.id, palette[tracks.length % palette.length]);
      tracks.push(p.id);
    }
  }
  selected ??= next.players[0]?.id;
  rankings(); activity(); controls(); paint();
}
function reset() {
  board = null; events = []; flights = []; tracks = []; colors.clear(); spots.clear();
  selected = null; replay = false; gap = false; elapsed = 0;
  rankings(); activity(); controls(); paint();
}
function setActive(visible) {
  if (active === visible) return;
  active = visible;
  if (!visible) { flights = []; replay = false; cancelAnimationFrame(frame); frame = 0; }
  last = 0;
  if (visible) { resize(); schedule(); }
}
function resize() {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  width = rect.width; height = rect.height;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
  paint();
}
function schedule() {
  if (frame || !active || document.hidden || paused) return;
  frame = requestAnimationFrame(t => {
    frame = 0;
    elapsed += last ? Math.min((t - last) / 1000, .1) : 0;
    last = t; paint(); schedule();
  });
}
function changeMotion(value) {
  paused = value; last = 0;
  if (paused) { flights = []; replay = false; cancelAnimationFrame(frame); frame = 0; }
  controls(); paint(); schedule();
}
get('orbit-motion').onclick = () => changeMotion(!paused);
media.addEventListener('change', e => changeMotion(e.matches));
get('orbit-replay').onclick = () => {
  flights = []; replay = !replay;
  if (replay) queue(board.recent_fills.slice().reverse().map(fillMove), .72);
  controls();
};
get('orbit-rank-toggle').onclick = e => {
  const toggle = e.currentTarget, open = toggle.getAttribute('aria-expanded') === 'true';
  toggle.setAttribute('aria-expanded', String(!open));
  get('orbit-rank-scroll').hidden = open;
};
document.addEventListener('visibilitychange', () => { last = 0; schedule(); });
new ResizeObserver(resize).observe(canvas);
canvas.onclick = e => {
  const hit = [...spots.entries()].find(([id, p]) => colors.has(id) && Math.hypot(e.offsetX - p.x, e.offsetY - p.y) < 17);
  if (hit) select(hit[0]);
};

function glow(x, y, radius, color) {
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
  gradient.addColorStop(0, color + 'fa'); gradient.addColorStop(.12, color + 'b0');
  gradient.addColorStop(.38, color + '46'); gradient.addColorStop(1, color + '00');
  ctx.fillStyle = gradient; ctx.fillRect(x-radius, y-radius, radius*2, radius*2);
}
function disc(x,y,r,color) {
  ctx.beginPath(); ctx.arc(x,y,r,0,tau); ctx.fillStyle = color; ctx.fill();
}
function ellipse(x,y,rx,ry,tilt,stroke,dotted = false) {
  ctx.beginPath(); ctx.ellipse(x,y,rx,ry,tilt,0,tau);
  ctx.setLineDash(dotted ? [2,6] : []); ctx.strokeStyle = stroke; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
}
function orbitPoint(cx,cy,rx,ry,tilt,angle) {
  const x = rx*Math.cos(angle), y = ry*Math.sin(angle);
  return {x:cx + x*Math.cos(tilt)-y*Math.sin(tilt), y:cy+x*Math.sin(tilt)+y*Math.cos(tilt)};
}
function beacon(x,y,color,kind) {
  glow(x,y,18,color); ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.beginPath();
  if (kind === stake) {
    ctx.moveTo(x,y-6); ctx.lineTo(x+5,y); ctx.lineTo(x,y+6); ctx.lineTo(x-5,y); ctx.closePath();
  } else {
    ctx.rect(x-4.5,y-4.5,9,9);
  }
  ctx.stroke(); disc(x,y,1.7,'#ffffff');
}
function comet(event, age) {
  const route = event.path.map(id => spots.get(id));
  if (route.some(p => !p) || route.length < 2) return;
  const color = ink[event.kind];
  // A peer payment follows one continuous path through its auction.
  const position = progress => {
    const v = Math.min(.99999, Math.max(0,progress)) * (route.length-1);
    const i = Math.floor(v), t = v-i, a = route[i], b = route[i+1];
    const bend = Math.sin(t*Math.PI)*.13;
    return {x:a.x+(b.x-a.x)*t-(b.y-a.y)*bend, y:a.y+(b.y-a.y)*t+(b.x-a.x)*bend};
  };
  let previous = position(age);
  for (let i=1; i<=12; i++) {
    const next = position(age-i*.013), alpha = (13-i)/13;
    ctx.strokeStyle = color; ctx.globalAlpha = alpha*.87; ctx.lineWidth = 3*alpha;
    ctx.beginPath(); ctx.moveTo(previous.x,previous.y); ctx.lineTo(next.x,next.y); ctx.stroke(); previous = next;
  }
  ctx.globalAlpha = 1;
  const head = position(age); glow(head.x,head.y,11,color); disc(head.x,head.y,2,'#fff');
  ctx.textAlign='center'; ctx.font='600 11px ui-monospace, monospace'; ctx.lineWidth=3;
  ctx.strokeStyle='#09121e'; ctx.strokeText(event.amount,head.x,head.y-10);
  ctx.fillStyle=color; ctx.fillText(event.amount,head.x,head.y-10);
}
function paint() {
  if (!active || !ctx || !width || !height) return;
  ctx.clearRect(0,0,width,height);
  // Deterministic stars keep the sky still while the market moves.
  for (let i=1; i<=130; i++) {
    const x = ((i*7919 % 997)/997)*width, y=((i*i*83+i*359)%991)/991*height;
    disc(x,y,i%13===0 ? 1.15 : .65, i%7===0 ? '#a4bac7' : '#486074');
  }
  const cx=width*.5, cy=height*.48, rx=Math.max(28,width*.355), ry=height*.31;
  for (const [scale, tilt] of [[.70,-.17],[.91,.12],[1.04,-.14]]) {
    ellipse(cx,cy,rx*scale,ry*scale,tilt,'#354455',scale===.70);
  }
  ellipse(cx,cy,rx*.52,ry*.53,-.18,'#273847',true);
  spots.set(treasury,{x:cx,y:cy});
  spots.set(stake,{x:cx-rx*.5,y:cy+ry*.30});
  spots.set(auction,{x:cx+rx*.48,y:cy-ry*.39});
  const treasuryColor='#a6a5ff';
  glow(cx,cy,64,treasuryColor);
  ctx.fillStyle='#d9d4ffc8'; ctx.beginPath();
  ctx.moveTo(cx-21,cy); ctx.quadraticCurveTo(cx-2,cy-1,cx,cy-15);
  ctx.quadraticCurveTo(cx+2,cy-1,cx+21,cy); ctx.quadraticCurveTo(cx+2,cy+1,cx,cy+15);
  ctx.quadraticCurveTo(cx-2,cy+1,cx-21,cy); ctx.fill();
  glow(cx,cy,23,'#dbd5ff'); disc(cx,cy,5,'#fff4ff');
  ellipse(cx,cy,17,6,-.53,'#bbadf591');
  const labels = [];
  function caption(x,y,name,value,color,priority=false) {
    ctx.textAlign='center'; ctx.font = '600 11px Inter, system-ui, sans-serif';
    const maxWidth = Math.min(width-12, Math.max(80,width*.33)), w = Math.min(maxWidth, Math.max(ctx.measureText(name).width, value ? value.length*7 : 0))+8;
    const h = value ? 29 : 16;
    for (const [dx,offset] of [[0,12],[0,-h-9],[0,26],[0,-h-22],[w*.45,10],[-w*.45,10]]) {
      const at = Math.min(width-w/2-2,Math.max(w/2+2,x+dx));
      const top = y + offset, left = at-w/2;
      if (top<0 || top+h>height || labels.some(b=>left<b.x+b.w && left+w>b.x && top<b.y+b.h && top+h>b.y)) continue;
      labels.push({x:left,y:top,w,h});
      ctx.fillStyle = priority ? '#f1f3ff' : '#d6e2eb'; ctx.fillText(name,at,top+9,maxWidth);
      if (value) { ctx.font='11px ui-monospace, monospace'; ctx.fillStyle=color; ctx.fillText(value,at,top+23,maxWidth); }
      break;
    }
  }
  caption(cx,cy,'Round treasury',null,treasuryColor,true);
  for (const [id, color] of [[stake,ink.stake],[auction,ink.auction]]) {
    const {x,y}=spots.get(id); beacon(x,y,color,id); caption(x,y,names.get(id),null,color,true);
  }
  const players = new Map((board?.players || []).map(p=>[p.id,p]));
  tracks.forEach((id,i) => {
    const level = i%3, scale = [.70,.91,1.04][level], tilt = [-.17,.12,-.14][level];
    const angle = -Math.PI*.78 + i * tau/Math.max(1,tracks.length) + elapsed*(.012+level*.0015);
    const pos=orbitPoint(cx,cy,rx*scale,ry*scale,tilt,angle);
    spots.set(id,pos);
    const color=colors.get(id); glow(pos.x,pos.y,15,color); disc(pos.x,pos.y,3.3,color); disc(pos.x,pos.y,1.35,'#fbfffe');
    if (id===selected) ellipse(pos.x,pos.y,8,8,0,color);
  });
  const ordered = selected ? [selected,...tracks.filter(id=>id!==selected)] : tracks;
  for (const id of ordered) {
    const mind=players.get(id), point=spots.get(id);
    if (point && mind) caption(point.x,point.y,mind.name,'$'+mind.score,colors.get(id));
  }
  flights = flights.filter(f=>elapsed-f.start < 2.25);
  for (const f of flights) if (elapsed>=f.start) comet(f.event,(elapsed-f.start)/2.25);
  if (replay && !flights.length) { replay=false; controls(); }
}
globalThis.marketOrbit = {update, reset, setActive};
