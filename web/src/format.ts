export const chains: Record<string, string> = {sol:'Solana',bsc:'BNB Chain',base:'Base',robinhood:'Robinhood',arc:'Arc'};
export const stageNames: Record<string, string> = {STRONG_REVIVAL:'Strong revival',CONFIRMED_REVIVAL:'Confirmed revival',EARLY_REVIVAL:'Early revival',WATCH:'Watch',IGNORE:'Below watch',REJECTED:'Rejected'};
export const stageName = (value: string) => stageNames[value] ?? value.replaceAll('_',' ').toLowerCase();
export function number(value: unknown, decimals = 0): string {
  return typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat('en-US',{maximumFractionDigits:decimals}).format(value) : '—';
}
export function compact(value: unknown, currency = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `${currency ? '$' : ''}${new Intl.NumberFormat('en-US',{notation:'compact',maximumFractionDigits:1}).format(value)}`;
}
export function price(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return `$${new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumSignificantDigits:5}).format(value)}`;
}
export function percent(value: unknown, fraction = false, signed = false): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const n = fraction ? value * 100 : value;
  return `${signed && n > 0 ? '+' : ''}${number(n,1)}%`;
}
export function shortAddress(address: string): string {return `${address.slice(0,5)}…${address.slice(-5)}`}
export function relative(timestamp: number | null | undefined, now = Date.now()/1000): string {
  if (timestamp == null || !Number.isFinite(timestamp)) return 'No observation';
  const seconds = Math.max(0,now-timestamp);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds/60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds/3600)}h ago`;
  return `${Math.floor(seconds/86400)}d ago`;
}
export function dateTime(timestamp: number | null | undefined): string {
  if (timestamp == null) return 'Unavailable';
  return new Date(timestamp*1000).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'});
}
export function humanKey(key: string): string {return key.replaceAll('_',' ').replace(/^./,s=>s.toUpperCase())}
export function tokenPath(chain: string,address: string): string {return `/tokens/${encodeURIComponent(chain)}/${encodeURIComponent(address)}`}
export function radarQuery(search: string): URLSearchParams {
  const query = new URLSearchParams(search);
  const score = Number(query.get('min_score') ?? 40);
  query.set('min_score',String(Number.isFinite(score) ? Math.max(0,Math.min(100,score)) : 40));
  query.set('page',String(Math.max(1,Number(query.get('page'))||1)));
  query.set('page_size','25');
  if (!['score','timestamp','market_cap','liquidity','volume_5m'].includes(query.get('sort') ?? '')) query.set('sort','score');
  if (!['asc','desc'].includes(query.get('order') ?? '')) query.set('order','desc');
  return query;
}
