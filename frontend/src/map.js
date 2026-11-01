import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { h, km } from './dom.js';

export const RISK_COLOR = { green: '#16a34a', yellow: '#eab308', orange: '#f97316', red: '#dc2626', unknown: '#64748b' };

export class MapView {
  constructor(el, { onDistrict, onRoute, onTilesFailed }) {
    this.cb = { onDistrict, onRoute, onTilesFailed };
    this.map = L.map(el, { preferCanvas: true, zoomControl: true }).setView([29.06, 76.09], 7);
    let errors = 0;
    const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18, attribution: '&copy; OpenStreetMap contributors',
    }).addTo(this.map);
    tiles.on('tileerror', () => { if (++errors === 3) this.cb.onTilesFailed(); });
    this.districtLayer = null;
    this.routeGroup = L.layerGroup().addTo(this.map);
    this.hl = { traversed: new Set(), risk: new Map() };
    setTimeout(() => this.map.invalidateSize(), 200);
  }

  setDistricts(fc) {
    if (this.districtLayer) this.districtLayer.remove();
    this.districtLayer = L.geoJSON(fc, {
      style: (f) => this.districtStyle(f.properties.district_id),
      onEachFeature: (f, layer) => {
        layer.bindTooltip(f.properties.district_name, { sticky: true });
        layer.on('click', () => this.cb.onDistrict(f.properties.district_id, f.properties.district_name));
      },
    }).addTo(this.map);
    this.districtLayer.bringToBack();
  }

  districtStyle(id) {
    const on = this.hl.traversed.has(id);
    const risk = this.hl.risk.get(id);
    return { color: on ? '#1d4ed8' : '#94a3b8', weight: on ? 2.2 : 1, fillColor: risk ? RISK_COLOR[risk] : (on ? '#bfdbfe' : '#e2e8f0'),
      fillOpacity: risk ? 0.35 : (on ? 0.25 : 0.12) };
  }

  setHighlight(traversedIds, riskByDistrict) {
    this.hl = { traversed: new Set(traversedIds), risk: new Map(Object.entries(riskByDistrict || {})) };
    if (this.districtLayer) this.districtLayer.setStyle((f) => this.districtStyle(f.properties.district_id));
  }

  drawRoutes(routes, selectedId, assessment) {
    this.routeGroup.clearLayers();
    if (!routes.length) return;
    const bounds = L.latLngBounds([]);
    const ll = (c) => c.map(([x, y]) => [y, x]);
    for (const r of routes) {
      const pts = ll(r.geometry.coordinates);
      pts.forEach((p) => bounds.extend(p));
      if (r.route_id === selectedId) continue;
      L.polyline(pts, { color: '#94a3b8', weight: 5, opacity: 0.85 }).addTo(this.routeGroup)
        .bindTooltip(`${r.label}: ${km(r.distance_m)} (click to select)`, { sticky: true })
        .on('click', () => this.cb.onRoute(r.route_id));
    }
    const sel = routes.find((r) => r.route_id === selectedId);
    if (sel) {
      const res = assessment?.results?.find((x) => x.route_id === sel.route_id);
      const sections = sel.sections?.length ? sel.sections : [{ status: 'matched', geometry: sel.geometry, district_id: null, length_m: sel.distance_m }];
      for (const s of sections) {
        const unknown = s.status === 'unknown';
        const warn = res?.findings?.filter((f) => f.district_id === s.district_id) ?? [];
        const worst = warn.length ? warn.map((f) => f.severity).sort((a, b) => ['yellow', 'orange', 'red'].indexOf(b) - ['yellow', 'orange', 'red'].indexOf(a))[0] : null;
        const line = L.polyline(ll(s.geometry.coordinates), {
          color: worst ? RISK_COLOR[worst] : (unknown ? '#475569' : '#2563eb'), weight: 7, opacity: 0.95, dashArray: unknown ? '2 10' : null, lineCap: 'round',
        }).addTo(this.routeGroup);
        line.bindPopup(h('div', { class: 'popup' },
          h('strong', {}, unknown ? 'Outside verified district boundaries' : (s.district_id ? sel.districts.find((d) => d.district_id === s.district_id)?.district_name ?? s.district_id : 'Route')),
          h('div', {}, `Length: ${km(s.length_m)}`),
          h('div', {}, unknown ? 'No district could be verified here, so weather risk is not assessed (unknown, not safe).'
            : (worst ? `${worst} warning applies` : 'No applicable warning evidence available / none found'))));
      }
      const c = sel.geometry.coordinates;
      L.circleMarker([c[0][1], c[0][0]], { radius: 8, color: '#fff', weight: 2, fillColor: '#16a34a', fillOpacity: 1 }).addTo(this.routeGroup).bindTooltip('Origin');
      L.circleMarker([c[c.length - 1][1], c[c.length - 1][0]], { radius: 8, color: '#fff', weight: 2, fillColor: '#dc2626', fillOpacity: 1 }).addTo(this.routeGroup).bindTooltip('Destination');
    }
    if (bounds.isValid()) this.map.fitBounds(bounds, { padding: [30, 30] });
  }
}
