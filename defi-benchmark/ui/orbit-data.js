// The contract settles in thousandths of STBL. Keep displayed transfers exact.
const stake = '@staking', auction = '@auction', treasury = '@treasury';
function milli(value) {
  const [whole, fraction = ''] = value.split('.');
  return BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, '0'));
}
function dollars(value) {
  return '$' + (value / 1000n).toLocaleString('en-US') + '.' + (value % 1000n).toString().padStart(3, '0');
}
function fillMove(fill) {
  const path = [fill.buyer_id, auction, fill.seller_id].filter(Boolean);
  return {kind: 'auction', title: 'Auction #' + fill.auction, path, time: fill.time,
    amount: dollars(milli(fill.total)), detail: fill.buyer + ' → ' + fill.seller,
    quantity: fill.quantity + ' LOT', fill: fill.n};
}
function changes(before, after) {
  const fills = (after.recent_fills || []).filter(f => f.n > before.fill_count).sort((a, b) => a.n - b.n);
  const events = fills.map(fillMove);
  const complete = after.fill_count != null && fills.length === after.fill_count - before.fill_count;
  const trading = new Map();
  for (const f of fills) {
    const sum = milli(f.total);
    for (const [id, delta] of [[f.buyer_id, -sum], [f.seller_id, sum]]) {
      if (id) trading.set(id, (trading.get(id) || 0n) + delta);
    }
  }
  const previous = new Map(before.players.map(p => [p.id, p]));
  for (const mind of after.players) {
    const old = previous.get(mind.id);
    const add = (kind, title, path, amount, detail) => events.push({kind, title, path, amount, detail, time: after.as_of});
    if (!old) add('grant', 'Round entry', [treasury, mind.id], '$100.000', 'Round treasury → ' + mind.name);
    const principal = milli(mind.staked) - milli(old?.staked || '0');
    const lots = BigInt(mind.staked_lots) - BigInt(old?.staked_lots || 0);
    for (const [delta, asset] of [[principal, 'STBL'], [lots, 'LOT']]) {
      if (!delta) continue;
      const deposit = delta > 0n, size = deposit ? delta : -delta;
      add('stake', deposit ? 'Net staked' : 'Net unstaked', deposit ? [mind.id, stake] : [stake, mind.id],
        asset === 'STBL' ? dollars(size) : size.toString() + ' LOT',
        deposit ? mind.name + ' → Staking' : 'Staking → ' + mind.name);
    }
    // Once trades and staked principal are accounted for, the only remaining cash inflow is a claim.
    if (complete) {
      const claim = milli(mind.cash) - milli(old?.cash || '100') + principal - (trading.get(mind.id) || 0n);
      if (claim > 0n) add('stake', 'Rewards claimed', [stake, mind.id], dollars(claim), 'Staking → ' + mind.name);
    }
  }
  return {events, missed: after.fill_count != null && !complete};
}
globalThis.orbitData = {stake, auction, treasury, fillMove, changes};
