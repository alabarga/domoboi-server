// Sincroniza el tema del admin (Unfold) con el de la app (clave 'domoboi-theme', por defecto oscuro).
(function () {
  var KEY = 'domoboi-theme', root = document.documentElement;
  function get() { try { return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'; } catch (e) { return 'dark'; } }
  function set(v) { try { localStorage.setItem(KEY, v); } catch (e) {} }
  var t = get();
  // Unfold guarda su preferencia con Alpine $persist: 'adminTheme' en JSON
  try { localStorage.setItem('adminTheme', JSON.stringify(t)); } catch (e) {}
  root.setAttribute('data-theme', t);
  // Si el usuario cambia el tema con el botón de Unfold, se guarda también para la app
  new MutationObserver(function () {
    var v = root.classList.contains('dark') ? 'dark' : 'light';
    root.setAttribute('data-theme', v);
    set(v);
  }).observe(root, { attributes: true, attributeFilter: ['class'] });
})();
