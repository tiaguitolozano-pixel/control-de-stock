// Lo que comparten las pantallas de carga (ingreso, egreso y trasvaso): números, transporte,
// renglones de productos (pallets + sueltas = total, kilos), saldo de pallets y envases del cliente y fotos.
window.Remito = (function () {
  const fmt = n => Number(n).toLocaleString('es-AR', {maximumFractionDigits: 3});
  const fmtCampo = n => Number(n).toLocaleString('es-AR', {maximumFractionDigits: 3, useGrouping: false});
  const r3 = n => Math.round(n * 1000) / 1000;
  const dmy = iso => iso ? iso.split('-').reverse().join('/') : '';
  function esc(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : t; return d.innerHTML; }
  function leerNumero(t) {
    // Igual que el programa: '1.234,5' = 1234,5 ; '1.000' = mil ; '2.5' = 2,5
    t = String(t || '').trim().replace(/\s/g, '');
    if (!t) return NaN;
    if (t.includes(',')) t = t.replace(/\./g, '').replace(',', '.');
    else if (/^-?[1-9]\d{0,2}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, '');
    return Number(t);
  }
  function textoPallets(cant, porPallet, unidad) {
    if (!porPallet || !(cant > 0)) return '';
    const enteros = Math.floor(cant / porPallet + 1e-9);
    let resto = cant - enteros * porPallet;
    if (resto < 1e-9) resto = 0;
    const palabra = enteros === 1 ? 'pallet' : 'pallets';
    if (!resto) return '= ' + enteros + ' ' + palabra;
    if (!enteros) return '= menos de 1 pallet';
    return '= ' + enteros + ' ' + palabra + ' + ' + fmt(resto) + ' ' + (unidad || '').toLowerCase();
  }

  // ---- transporte: autocompletar con el último viaje del chofer, patentes y contenedor
  function transporte(previos) {
    const chofer = document.getElementById('chofer');
    chofer.addEventListener('change', () => {
      const c = previos.choferes.find(x => x.chofer.toLowerCase() === chofer.value.trim().toLowerCase());
      if (!c) return;
      for (const campo of ['transporte', 'patente_camion', 'patente_semi']) {
        const input = document.getElementById(campo);
        if (!input.value.trim()) input.value = c[campo];
      }
    });
    document.querySelectorAll('#patente_camion, #patente_semi').forEach(i =>
      i.addEventListener('blur', () => { i.value = i.value.toUpperCase().replace(/[^A-Z0-9]/g, ''); }));

    // El último dígito del contenedor (dígito de control, norma ISO 6346) tiene que coincidir.
    const contenedor = document.getElementById('contenedor');
    const avisoCont = document.getElementById('aviso-contenedor');
    function digitoControl(codigo) {
      const valores = {};
      let v = 10;
      for (const letra of 'ABCDEFGHIJKLMNOPQRSTUVWXYZ') { if (v % 11 === 0) v++; valores[letra] = v++; }
      let suma = 0;
      for (let i = 0; i < 10; i++) {
        const c = codigo[i];
        suma += (/\d/.test(c) ? Number(c) : valores[c]) * Math.pow(2, i);
      }
      return (suma % 11) % 10;
    }
    function revisarContenedor() {
      const compacto = contenedor.value.toUpperCase().replace(/[^A-Z0-9]/g, '');
      avisoCont.textContent = '';
      if (/^[A-Z]{4}\d{6,7}$/.test(compacto)) {
        contenedor.value = compacto;
        const digito = digitoControl(compacto);
        if (compacto.length === 10) avisoCont.textContent = 'Falta el último dígito: según el número, sería ' + digito + '.';
        else if (Number(compacto[10]) !== digito) avisoCont.textContent = 'Revisá el número: el último dígito no coincide (debería ser ' + digito + '). Puede haber un error de tipeo.';
      }
    }
    contenedor.addEventListener('blur', revisarContenedor);
    if (contenedor.value) revisarContenedor();
  }

  // ---- una tabla de renglones de productos
  // modo 'INGRESO': lote y vencimiento se escriben. modo 'EGRESO': se elige un lote con stock.
  // prefijo: para tener dos tablas en la misma pantalla (el trasvaso), los campos de una llevan prefijo.
  function tabla(op) {
    const cuerpo = op.cuerpo, esEgreso = op.modo === 'EGRESO', prefijo = op.prefijo || '';
    const campo = n => prefijo + n;
    const productos = () => op.productos() || [];
    function productoDe(id) { return productos().find(x => String(x.id) === String(id)); }

    function opcionesProducto(sel) {
      let html = '<option value="">Elegí un producto…</option>';
      for (const p of productos()) {
        const s = String(p.id) === String(sel) ? ' selected' : '';
        const extra = esEgreso ? ` · stock ${fmt(p.stock)}` : '';
        html += `<option value="${p.id}"${s}>${esc(p.codigo)} - ${esc(p.descripcion)} (${esc(p.unidad)})${extra}</option>`;
      }
      return html;
    }

    function opcionesLote(prodId, sel) {
      const p = productoDe(prodId);
      if (!p) return '<option value="">—</option>';
      if (!p.lotes.length) return '<option value="">Sin stock</option>';
      let html = '';
      p.lotes.forEach(l => {
        const s = String(l.id) === String(sel) ? ' selected' : '';
        let txt = l.lote ? 'Lote ' + l.lote : 'Sin lote';
        if (l.vencimiento) txt += ' · vence ' + dmy(l.vencimiento) + (l.vencimiento < op.hoy ? ' (VENCIDO)' : (l.vencimiento <= op.limite ? ' (por vencer)' : ''));
        txt += ' · hay ' + fmt(l.stock) + ' ' + p.unidad;
        const pal = textoPallets(l.stock, p.por_pallet, p.unidad);
        if (pal) txt += ' (' + pal.slice(2) + ')';
        html += `<option value="${l.id}"${s}>${esc(txt)}</option>`;
      });
      return html;
    }

    // Lo cargado: [{producto, cantidad, lote_id, lote, vencimiento}] (solo renglones con producto y cantidad).
    function lineas() {
      const res = [];
      cuerpo.querySelectorAll('tr').forEach(tr => {
        const p = productoDe(tr.querySelector(`select[name="${campo('producto_id')}"]`).value);
        const cantidad = leerNumero(tr.querySelector(`input[name="${campo('cantidad')}"]`).value);
        if (!p || !(cantidad > 0)) return;
        const loteSel = tr.querySelector(`select[name="${campo('lote_id')}"]`);
        const l = loteSel ? p.lotes.find(x => String(x.id) === loteSel.value) : null;
        res.push({
          producto: p, cantidad,
          lote_id: l ? l.id : null,
          lote: l ? l.lote : (tr.querySelector(`input[name="${campo('lote')}"]`) || {}).value,
          vencimiento: l ? l.vencimiento : (tr.querySelector(`input[name="${campo('vencimiento')}"]`) || {}).value,
        });
      });
      return res;
    }

    // Totales al pie: cantidad por tipo de envase y kilos (kilos por unidad × cantidad).
    function resumen() {
      const porEnvase = new Map();
      let kilos = 0, sinKilos = 0;
      for (const l of lineas()) {
        const p = l.producto;
        const clave = (p.unidad || '').toLowerCase();
        porEnvase.set(clave, (porEnvase.get(clave) || 0) + l.cantidad);
        if (p.kilos) kilos += l.cantidad * p.kilos; else sinKilos++;
      }
      return {porEnvase, kilos: r3(kilos), sinKilos};
    }
    function actualizarTotales() {
      const t = resumen();
      if (op.totalCantidades) op.totalCantidades.innerHTML =
        [...t.porEnvase].map(([u, c]) => `<div>${fmt(r3(c))} ${esc(u)}</div>`).join('') || '—';
      if (op.totalKilos) op.totalKilos.innerHTML = (t.kilos ? fmt(t.kilos) + ' kg' : '—') +
        (t.sinKilos ? `<div class="ayuda">${t.sinKilos === 1 ? '1 producto' : t.sinKilos + ' productos'} sin kilos</div>` : '');
      if (op.alCambiar) op.alCambiar();
    }

    function agregar(r) {
      r = r || {};
      const tr = document.createElement('tr');
      const celdaLote = esEgreso
        ? `<td><select name="${campo('lote_id')}">${opcionesLote(r.producto_id, r.lote_id)}</select></td>`
        : `<td><input name="${campo('lote')}" value="${esc(r.lote)}" placeholder="Sin lote"></td>
           <td><input type="date" name="${campo('vencimiento')}" value="${esc(r.vencimiento)}"></td>`;
      tr.innerHTML = `<td><select name="${campo('producto_id')}">${opcionesProducto(r.producto_id)}</select><div class="info-prod"></div></td>
        ${celdaLote}
        <td><input class="pallets-input" inputmode="decimal" placeholder="—" aria-label="Pallets completos"></td>
        <td><input class="sueltas-input" inputmode="decimal" placeholder="—" aria-label="Unidades sueltas"></td>
        <td><input name="${campo('cantidad')}" inputmode="decimal" placeholder="0" value="${esc(r.cantidad)}" aria-label="Cantidad total">
            <div class="conv"></div></td>
        <td class="num kilos-renglon"></td>
        <td><button type="button" class="boton chico secundario" title="Quitar">✕</button></td>`;
      const selProd = tr.querySelector('select');
      const cant = tr.querySelector(`input[name="${campo('cantidad')}"]`);
      const pal = tr.querySelector('.pallets-input');
      const sue = tr.querySelector('.sueltas-input');
      const conv = tr.querySelector('.conv');
      const info = tr.querySelector('.info-prod');
      const celdaKilos = tr.querySelector('.kilos-renglon');

      function porPallet() { const p = productoDe(selProd.value); return p && p.por_pallet ? Number(p.por_pallet) : 0; }
      function actualizarConv() {
        // Debajo del total: en qué unidad se cuenta. Al costado: los kilos del renglón.
        const p = productoDe(selProd.value);
        const t = leerNumero(cant.value);
        conv.textContent = p ? 'en ' + (p.unidad || '').toLowerCase() : '';
        if (!p) celdaKilos.textContent = '';
        else if (!p.kilos) { celdaKilos.innerHTML = '<span class="ayuda" title="Cargá los kilos por unidad en el producto">sin kilos</span>'; }
        else celdaKilos.innerHTML = (t > 0 ? '<strong>' + fmt(r3(t * p.kilos)) + ' kg</strong>' : '—');
        actualizarTotales();
      }
      // Pallets completos + sueltas -> total (mientras se escribe)
      function desdePartes() {
        const pp = porPallet();
        if (!pp) return;
        const P = leerNumero(pal.value), s = leerNumero(sue.value);
        const total = (P > 0 ? P : 0) * pp + (s > 0 ? s : 0);
        cant.value = total > 0 ? fmtCampo(r3(total)) : '';
        actualizarConv();
      }
      // Total -> pallets completos + sueltas. También ordena: si las sueltas llegan a un pallet, pasan a pallets.
      function desdeTotal() {
        const pp = porPallet(), t = leerNumero(cant.value);
        if (!pp || !(t > 0)) { pal.value = ''; sue.value = ''; actualizarConv(); return; }
        const P = Math.floor(t / pp + 1e-9);
        const s = r3(t - P * pp);
        pal.value = String(P);
        sue.value = s > 1e-9 ? fmtCampo(s) : '0';
        actualizarConv();
      }
      function prepararPallets() {
        const p = productoDe(selProd.value);
        const pp = porPallet();
        const ayuda = pp ? `1 pallet = ${fmt(pp)} unidades`
                         : 'Este producto no tiene cargadas las unidades por pallet: escribí la cantidad total';
        for (const i of [pal, sue]) { i.disabled = !pp; i.placeholder = pp ? '0' : '—'; i.title = ayuda; }
        // Los datos del producto, debajo del nombre.
        if (!p) info.textContent = '';
        else {
          const u = (p.unidad || '').toLowerCase();
          info.textContent = (pp ? `${fmt(pp)} ${u} por pallet` : 'sin unidades por pallet') + ' · ' +
                             (p.kilos ? `${fmt(p.kilos)} kg por unidad` : 'sin kilos por unidad');
        }
        desdeTotal();
      }
      cant.addEventListener('input', desdeTotal);
      pal.addEventListener('input', desdePartes);
      sue.addEventListener('input', desdePartes);
      // Al salir del casillero: las sueltas que completan pallets pasan a pallets (el total no cambia).
      pal.addEventListener('change', desdeTotal);
      sue.addEventListener('change', desdeTotal);
      selProd.addEventListener('change', () => {
        if (esEgreso) tr.querySelector(`select[name="${campo('lote_id')}"]`).innerHTML = opcionesLote(selProd.value);
        prepararPallets();
      });
      if (esEgreso) tr.querySelector(`select[name="${campo('lote_id')}"]`).addEventListener('change', actualizarTotales);
      else tr.querySelectorAll(`input[name="${campo('lote')}"], input[name="${campo('vencimiento')}"]`)
        .forEach(i => i.addEventListener('change', actualizarTotales));
      tr.querySelector('button').addEventListener('click', () => {
        tr.remove();
        if (!cuerpo.children.length) agregar();
        actualizarTotales();
      });
      cuerpo.appendChild(tr);
      prepararPallets();
      return tr;
    }

    function cargar(renglones) {
      cuerpo.innerHTML = '';
      if (renglones && renglones.length) renglones.forEach(agregar); else agregar();
      actualizarTotales();
    }

    return {agregar, cargar, lineas, resumen, actualizar: actualizarTotales};
  }

  // ---- insumos: saldo de pallets del cliente de la empresa elegida
  // sale: true si el remito saca insumos del depósito (egreso).
  function insumosCliente(empresa, sale, saldos) {
    const inputs = document.querySelectorAll('input.insumo-cliente');
    function actualizar() {
      inputs.forEach(inp => {
        const celda = document.getElementById('saldo-insumo-' + inp.dataset.insumo);
        if (!empresa.value) { celda.textContent = ''; return; }
        const hay = Number(saldos()[inp.dataset.insumo] || 0);
        const mov = leerNumero(inp.value);
        let txt = 'De esta empresa hay ' + fmt(hay) + ' en el depósito';
        celda.classList.remove('negativo');
        if (mov > 0) {
          const queda = r3(sale ? hay - mov : hay + mov);
          txt += ' → quedan ' + fmt(queda);
          if (queda < 0) celda.classList.add('negativo');
        }
        celda.textContent = txt + '.';
      });
    }
    inputs.forEach(inp => inp.addEventListener('input', actualizar));
    return actualizar;
  }

  // ---- envases vacíos y sucios del cliente (ingreso, egreso y trasvaso)
  // Cada casillero (input.envase) tiene data-envase, data-estado (VACIO o SUCIO) y data-signo
  // (1 si suma a lo que la empresa tiene en el depósito, -1 si resta). saldos(): {envase_id: {VACIO, SUCIO}}.
  function envasesCliente(empresa, saldos) {
    const inputs = [...document.querySelectorAll('input.envase')];
    function actualizar() {
      const porEnvase = {};
      inputs.forEach(i => (porEnvase[i.dataset.envase] = porEnvase[i.dataset.envase] || []).push(i));
      for (const [id, lista] of Object.entries(porEnvase)) {
        const celda = document.getElementById('saldo-envase-' + id);
        if (!celda) continue;
        celda.classList.remove('negativo');
        if (!empresa.value) { celda.textContent = ''; continue; }
        const hay = saldos()[id] || {};
        celda.textContent = 'De esta empresa: ' + lista.map(i => {
          const antes = Number(hay[i.dataset.estado] || 0);
          const mov = leerNumero(i.value);
          let txt = (i.dataset.estado === 'VACIO' ? 'vacíos' : 'sucios') + ' hay ' + fmt(antes);
          if (mov > 0) {
            const queda = r3(antes + Number(i.dataset.signo) * mov);
            txt += ' → quedan ' + fmt(queda);
            if (queda < 0) celda.classList.add('negativo');
          }
          return txt;
        }).join(' · ') + '.';
      }
    }
    inputs.forEach(i => i.addEventListener('input', actualizar));
    return actualizar;
  }

  // ---- fotos: se achican en el navegador antes de mandarlas (ver static/fotos.js)
  function fotos(input, form, previa) {
    function mostrarPrevia(archivos) {
      previa.innerHTML = '';
      if (archivos === null) { previa.textContent = 'Preparando las fotos… (esperá un segundo antes de guardar)'; return; }
      let total = 0;
      archivos.forEach(a => {
        total += a.size;
        if (!/^image\/(jpeg|png|webp|gif)$/.test(a.type)) return;
        const img = document.createElement('img');
        img.src = URL.createObjectURL(a);
        img.alt = a.name;
        previa.appendChild(img);
      });
      if (archivos.length) {
        const txt = document.createElement('div');
        txt.className = 'ayuda';
        txt.textContent = (archivos.length === 1 ? '1 foto' : archivos.length + ' fotos') + ' · ' +
                          fmt(r3(total / 1048576)) + ' MB en total';
        previa.appendChild(txt);
      }
    }
    prepararFotos(input, form, mostrarPrevia);
  }

  return {fmt, fmtCampo, r3, dmy, esc, leerNumero, textoPallets, transporte, tabla, insumosCliente, envasesCliente, fotos};
})();
