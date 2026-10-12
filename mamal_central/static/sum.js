(function () {
  var inputs = document.querySelectorAll('.sale');
  var out = document.getElementById('total');
  if (!out) return;
  var fa = new Intl.NumberFormat('fa-IR');
  function update() {
    var sum = 0;
    inputs.forEach(function (i) { sum += parseInt(i.value, 10) || 0; });
    out.textContent = fa.format(sum);
  }
  inputs.forEach(function (i) { i.addEventListener('input', update); });
  update();
})();
