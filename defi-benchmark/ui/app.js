const el = id => document.getElementById(id);
const {appChannel: channel, appHostOrigin: origin} = document.documentElement.dataset;
let board, mode = 'standings', selected = 'current', page = 1, sequence = 0;
let waiting = null, timeout;
let setup, signerChoice = '', actionPending = null;
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
  const market = Boolean(board?.demand);
  el('market-summary').hidden = ranked || !market;
  el('fills').hidden = ranked || !market;
  if (!ranked && market) {
    const bid = board.demand;
    label('market-summary', board.primary_remaining + ' of ' + board.supply + ' treasury LOT remain · Consumer bid ' +
      bid.price + ' STBL for up to ' + bid.remaining + ' LOT this minute · ' + bid.retired + ' LOT consumed');
    label('fill-count', board.fill_count + ' fills · ' + board.turnover + ' STBL traded');
    const fills = board.recent_fills.map(fill => {
      const row = document.createElement('tr');
      row.append(cell('td', new Date(fill.time * 1000).toISOString().slice(11, 19)), cell('td', '#' + fill.auction),
        cell('td', fill.buyer), cell('td', fill.seller), cell('td', String(fill.quantity)), cell('td', fill.price));
      return row;
    });
    el('fill-rows').replaceChildren(...fills);
  }
  const columns = ranked ? ['Rank', 'Omega mind', 'Liquid', 'Staked STBL', 'Rewards', 'Score', 'Gain / loss']
    : ['Auction', 'Seller', 'LOT left', ...(market ? ['LOT sold'] : []), 'Price', 'Floor', 'Closes (UTC)', 'Status'];
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
        ...(market ? [cell('td', item.sold)] : []),
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
function setupVisible(visible) {
  el('setup').hidden = !visible;
  for (const id of ['overview', 'round-nav', 'results']) el(id).hidden = visible;
}
function renderSetup(info) {
  setup = info;
  setupVisible(true);
  label('chain', info.network + ' / ' + info.shard);
  label('setup-title', ['prepared', 'pending'].includes(info.phase) ? 'Installing the benchmark' : 'Set up the benchmark');
  const keys = info.keys || [];
  signerChoice = info.selected || '';
  el('signer').replaceChildren(...keys.map(key => {
    const signer = info.signers?.[key];
    let caption = key === 'host' ? 'Host-configured signer' : key;
    if (signer) {
      const address = signer.address;
      caption = [signer.name, address.slice(0, 6) + '..' + address.slice(-4)].filter(Boolean).join(' ');
    }
    const option = cell('option', caption);
    option.value = key;
    if (signer) option.title = signer.address;
    return option;
  }));
  el('signer').value = signerChoice;
  el('signer').disabled = !!actionPending;
  el('pick').hidden = !keys.length;
  el('funds').hidden = !info.address;
  label('address', info.address || '');
  label('balance', (info.balance || '0') + ' native base units');
  el('fee').hidden = !info.max_fee;
  label('fee', 'Maximum deployment fee: ' + (info.max_fee || '0') + ' native base units.');
  el('deploy-id').hidden = !info.id;
  label('deploy-id', 'Deploy ID: ' + (info.id || ''));
  let action = '', title = '', guidance = '';
  if (info.phase === 'unavailable') guidance = info.message;
  else if (info.phase === 'pending') guidance = 'Submitted. This page checks for finalization and opens the leaderboard when it is ready.';
  else if (info.phase === 'prepared') {
    guidance = 'The signed transaction is saved. It can be sent again with the same deploy ID.';
    action = 'resume-deployment'; title = 'Resume deployment';
  } else if (!info.operator) guidance = 'The local Hive operator can deploy the benchmark from this screen.';
  else if (!keys.length) {
    guidance = 'Create a signing key in the Hive wallet, then fund its address for chain fees.';
    action = 'create-signer'; title = 'Create signing key';
  } else {
    guidance = info.message || 'Deploy the bundled Rholang contract to start the hourly staking and auction rounds.';
    if (info.address && BigInt(info.balance) < BigInt(info.max_fee)) {
      guidance = 'Fund the address below with native tokens for deployment, then refresh its balance.';
    }
    action = 'deploy-benchmark'; title = 'Deploy benchmark';
  }
  label('guidance', guidance);
  const button = el('setup-action');
  button.hidden = !info.operator || !action;
  button.dataset.action = action;
  button.textContent = title;
  button.disabled = !!actionPending || (action === 'deploy-benchmark' && !!info.address && BigInt(info.balance) < BigInt(info.max_fee));
}
function load() {
  if (actionPending) return;
  if (!channel || !origin) { notify('Open DeFi Benchmark inside OmegaHive to see finalized standings.'); return; }
  clearTimeout(timeout);
  waiting = 'board-' + (++sequence);
  const focus = setup && signerChoice ? 'setup:' + signerChoice : 'round:' + selected + ':' + page;
  send({type:'app-request', id:waiting, op:'view', body:{focus, stage:'unfold'}});
  timeout = setTimeout(() => {
    const text = 'The Hive has not returned a fresh snapshot. Refresh to try again.';
    if (setup) label('setup-notice', text); else notify(text);
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
  if (msg.type === 'app-reply' && actionPending && msg.id === actionPending) {
    actionPending = null;
    label('setup-notice', msg.result?.message || msg.error || '');
    load();
    return;
  }
  if (msg.type !== 'app-reply' || !waiting || msg.id !== waiting) return;
  clearTimeout(timeout); waiting = null;
  const config = msg.result?.items?.find(i => i.kind === 'setup');
  if (msg.ok && config?.setup) { renderSetup(config.setup); return; }
  setup = null; signerChoice = '';
  setupVisible(false);
  const round = msg.result?.items?.find(i => i.kind === 'round');
  if (!msg.ok || !round?.board) {
    reset(); notify(round?.summary || msg.error || 'Finalized standings are unavailable.'); return;
  }
  board = round.board; page = board.page; render();
});
el('refresh').onclick = () => {
  actionPending = null;
  label('setup-notice', '');
  load();
};
el('signer').onchange = () => {
  signerChoice = el('signer').value;
  el('setup-action').disabled = true;
  label('setup-notice', '');
  load();
};
el('setup-action').onclick = () => {
  const action = el('setup-action').dataset.action;
  if (actionPending || !setup?.operator || !action) return;
  clearTimeout(timeout); waiting = null;
  actionPending = 'setup-' + (++sequence);
  el('setup-action').disabled = true;
  el('signer').disabled = true;
  label('setup-notice', 'Waiting for confirmation from OmegaHive…');
  const params = action === 'deploy-benchmark' ? {signer: setup.selected, max_fee: setup.max_fee} : {};
  send({type:'app-request', id:actionPending, op:'act', body:{action, items:[], params}});
};
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
