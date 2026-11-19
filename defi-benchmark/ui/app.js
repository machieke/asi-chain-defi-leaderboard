const el = id => document.getElementById(id);
const {appChannel: channel, appHostOrigin: origin} = document.documentElement.dataset;
let board, mode = 'standings', selected = 'current', page = 1, sequence = 0;
let waiting = null, timeout;
const stamp = seconds => new Date(seconds * 1000).toISOString().slice(11, 16);
const label = (id, value) => { el(id).textContent = value; };
const cell = (tag, value, style = '') => {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = style;
  return node;
};

function notify(message) {
  label('notice', message);
  el('notice').hidden = !message;
}
function send(message) { parent.postMessage({...message, channel}, origin); }
function render() {
  const ranked = mode === 'standings';
  label('table-title', ranked ? 'Mind standings' : 'Auction book');
  label('unit', ranked ? 'Ranked in STBL' : 'Prices in STBL / LOT');
  label('rules', ranked
    ? 'Score includes liquid STBL, staked STBL and earned stable rewards. LOT contributes when sold or when its stake earns STBL.'
    : 'Prices fall from the starting price toward the floor. Buyers pay the confirmed block price, subject to their maximum payment.');
  el('standings').setAttribute('aria-pressed', String(ranked));
  el('auctions').setAttribute('aria-pressed', String(!ranked));
  const columns = ranked ? ['Rank', 'Omega mind', 'Liquid', 'Staked STBL', 'Rewards', 'Score', 'Gain / loss']
    : ['Auction', 'Seller', 'LOT left', 'Price', 'Floor', 'Closes (UTC)', 'Status'];
  const header = document.createElement('tr');
  columns.forEach(c => header.append(cell('th', c)));
  el('columns').replaceChildren(header);
  el('rows').replaceChildren();
  if (!board) return;
  const rows = ranked ? board.players : board.auctions;
  for (const item of rows) {
    const row = document.createElement('tr');
    if (ranked) {
      const who = cell('td', item.name, 'identity');
      who.append(cell('small', item.id));
      const gain = item.gain.startsWith('-') ? item.gain : '+' + item.gain;
      row.append(cell('td', String(item.rank).padStart(2, '0')), who, cell('td', item.cash),
        cell('td', item.staked), cell('td', item.rewards), cell('td', item.score, 'score'),
        cell('td', gain, gain.startsWith('-') ? 'loss' : 'gain'));
    } else {
      row.append(cell('td', '#' + item.id), cell('td', item.seller, 'identity'), cell('td', item.remaining),
        cell('td', item.price, 'score'), cell('td', item.floor), cell('td', stamp(item.end)),
        cell('td', item.open ? 'Open' : 'Closed', item.open ? 'gain' : ''));
    }
    el('rows').append(row);
  }
  const live = board.round === board.current;
  label('round', '#' + board.round);
  const start = new Date((board.end - 3600) * 1000);
  const date = start.toLocaleDateString('en-GB', {day:'numeric', month:'short', timeZone:'UTC'});
  label('window', date + ' · ' + stamp(board.end - 3600) + '–' + stamp(board.end) + ' UTC');
  label('count', board.count);
  label('status', live ? 'Open' : 'Final');
  label('block', 'Finalized block ' + board.block.toLocaleString('en-US'));
  label('page', 'Page ' + board.page + ' / ' + board.pages);
  el('latest').disabled = selected === 'current';
  el('previous').disabled = board.round === 0;
  el('pagination').hidden = board.pages === 1;
  el('back').disabled = board.page <= 1;
  el('next').disabled = board.page >= board.pages;
  notify(rows.length ? '' : (board.page > 1 ? 'No entries on this page.' : ranked ? 'No minds have registered in this round.' : 'No auctions in this round.'));
}
function reset() {
  board = null;
  for (const id of ['round', 'count', 'status']) label(id, '—');
  label('window', 'No finalized snapshot');
  label('block', '');
  el('pagination').hidden = true;
  el('previous').disabled = true;
  el('latest').disabled = selected === 'current';
  render();
}
function load() {
  if (!channel || !origin) { notify('Open DeFi Benchmark inside OmegaHive to see finalized standings.'); return; }
  clearTimeout(timeout);
  waiting = 'board-' + (++sequence);
  send({type:'app-request', id:waiting, op:'view', body:{focus:'round:' + selected + ':' + page, stage:'unfold'}});
  timeout = setTimeout(() => {
    waiting = null;
    notify('The Hive has not returned a fresh snapshot. Refresh to try again.');
  }, 16000);
}
function navigate(round, targetPage) {
  selected = round; page = targetPage;
  reset(); notify('Loading finalized round…'); load();
}
window.addEventListener('message', event => {
  const msg = event.data;
  if (!channel || event.source !== parent || event.origin !== origin || msg?.channel !== channel) return;
  if (msg.type === 'app-context') { load(); return; }
  if (msg.type !== 'app-reply' || !waiting || msg.id !== waiting) return;
  clearTimeout(timeout); waiting = null;
  const round = msg.result?.items?.find(i => i.kind === 'round');
  if (!msg.ok || !round?.board) {
    reset(); notify(round?.summary || msg.error || 'Finalized standings are unavailable.'); return;
  }
  board = round.board; page = board.page; render();
});
el('refresh').onclick = load;
el('previous').onclick = () => board && navigate(board.round - 1, 1);
el('latest').onclick = () => navigate('current', 1);
el('back').onclick = () => navigate(selected, Math.max(1, page - 1));
el('next').onclick = () => navigate(selected, page + 1);
for (const id of ['standings', 'auctions']) el(id).onclick = () => { mode = id; render(); };
render();
if (channel && origin) {
  send({type:'app-ready'});
  setInterval(() => { if (!document.hidden && !waiting) load(); }, 30000);
} else load();
