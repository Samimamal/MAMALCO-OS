(function () {
  var el = document.getElementById('map');
  var data = JSON.parse(document.getElementById('map-data').textContent);
  var colors = { form: '#1f5f4a', village: '#b06a1b', lodge: '#3d5a99', other: '#666' };
  var map = L.map(el, { scrollWheelZoom: false });
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);
  var bounds = [];
  function esc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
  data.sites.forEach(function (s) {
    var poly = L.polygon(s.polygon, { color: colors[s.category] || '#666', weight: 2, fillOpacity: 0.15, dashArray: '6 4' }).addTo(map);
    poly.bindPopup('<strong>' + esc(s.name) + '</strong><br>' + esc(s.owner) +
      '<br>مساحت اعلامی: ' + esc(s.declared || '—') + ' m² · نقاط: ' + esc(s.computed) + ' m²');
    s.polygon.forEach(function (p) { bounds.push(p); });
  });
  data.points.forEach(function (p) {
    var c = colors[p.category] || '#666';
    L.circleMarker([p.lat, p.lng], {
      radius: 8, color: c, weight: 2, fillColor: c, fillOpacity: p.public ? 0.9 : 0.15
    }).addTo(map).bindPopup('<strong>' + esc(p.code) + '</strong> ' + esc(p.name) + '<br><a href="' + esc(p.url) + '">باز کردن</a>');
    bounds.push([p.lat, p.lng]);
  });
  if (bounds.length) { map.fitBounds(bounds, { padding: [30, 30], maxZoom: 17 }); }
  else { map.setView([37.0415, 45.0995], 14); }  // Oshnavieh
})();
