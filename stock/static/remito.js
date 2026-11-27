// Lo que comparten las pantallas de carga (ingreso, egreso y trasvaso): números, transporte,
// renglones de productos (cantidad, en cuántos pallets va y kilos), saldo de pallets y envases del cliente y fotos.
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
  // En cuántos pallets va una cantidad, contando el último aunque no esté lleno (así se arman y se cargan):
  // 80 bolsas con 48 por pallet = 2 pallets, uno completo y otro con 32. Igual que formato.texto_pallets.
  function contarPallets(cant, porPallet) {
    if (!(porPallet > 0) || !(cant > 0)) return null;
    const completos = Math.floor(cant / porPallet + 1e-9);
    let resto = r3(cant - completos * porPallet);
    if (resto < 1e-9) resto = 0;
    return {total: completos + (resto ? 1 : 0), completos, resto};
  }
  const palabraPallets = n => n === 1 ? 'pallet' : 'pallets';
  // El envase en singular, para 1 unidad: 'tambores de chapa' -> 'tambor de chapa'. Igual que formato.singular.
  function singular(unidad) {
    const partes = String(unidad || '').split(' ');
    for (const [fin, cambio] of [['ones', 'ón'], ['ores', 'or'], ['ades', 'ad'], ['les', 'l'], ['nes', 'n'], ['res', 'r'], ['s', '']]) {
      if (partes[0].toLowerCase().endsWith(fin) && partes[0].length > fin.length + 1) {
        partes[0] = partes[0].slice(0, -fin.length) + cambio;
        break;
      }
    }
    return partes.join(' ');
  }
  // El pallet incompleto: '1 completo + 1 de 32 bolsas', o 'de 32 bolsas' si es el único.
  function detallePallets(c, unidad) {
    if (!c || !c.resto) return '';
    const u = unidad ? ' ' + (c.resto === 1 ? singular(unidad) : unidad).toLowerCase() : '';
    if (!c.completos) return 'de ' + fmt(c.resto) + u;
    return c.completos + (c.completos === 1 ? ' completo' : ' completos') + ' + 1 de ' + fmt(c.resto) + u;
  }
  function textoPallets(cant, porPallet, unidad) {
    const c = contarPallets(cant, porPallet);
    if (!c) return '';
    const base = c.total + ' ' + palabraPallets(c.total);
    if (!c.resto) return base;
    return c.completos ? base + ' (' + detallePallets(c, unidad) + ')' : base + ' ' + detallePallets(c, unidad);
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
        const c = contarPallets(l.stock, Number(p.por_pallet));
        if (c) txt += ' (' + c.total + ' ' + palabraPallets(c.total) + ')';
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

    // Totales al pie: cantidad por tipo de envase, pallets (cada renglón con su pallet incompleto) y kilos
    // (kilos por unidad × cantidad).
    function resumen() {
      const porEnvase = new Map();
      let kilos = 0, sinKilos = 0, pallets = 0, sinPallets = 0;
      for (const l of lineas()) {
        const p = l.producto;
        const clave = (p.unidad || '').toLowerCase();
        porEnvase.set(clave, (porEnvase.get(clave) || 0) + l.cantidad);
        if (p.kilos) kilos += l.cantidad * p.kilos; else sinKilos++;
        const c = contarPallets(l.cantidad, Number(p.por_pallet));
        if (c) pallets += c.total; else sinPallets++;
      }
      return {porEnvase, kilos: r3(kilos), sinKilos, pallets, sinPallets};
    }
    const sinDato = (n, que) => `<div class="ayuda">${n === 1 ? '1 producto' : n + ' productos'} sin ${que}</div>`;
    function actualizarTotales() {
      const t = resumen();
      if (op.totalCantidades) op.totalCantidades.innerHTML =
        [...t.porEnvase].map(([u, c]) => `<div>${fmt(r3(c))} ${esc(u)}</div>`).join('') || '—';
      if (op.totalPallets) op.totalPallets.innerHTML = (t.pallets ? t.pallets + ' ' + palabraPallets(t.pallets) : '—') +
        (t.sinPallets ? sinDato(t.sinPallets, 'unidades por pallet') : '');
      if (op.totalKilos) op.totalKilos.innerHTML = (t.kilos ? fmt(t.kilos) + ' kg' : '—') +
        (t.sinKilos ? sinDato(t.sinKilos, 'kilos') : '');
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
        <td><input name="${campo('cantidad')}" inputmode="decimal" placeholder="0" value="${esc(r.cantidad)}" aria-label="Cantidad">
            <div class="conv"></div></td>
        <td class="pallets-renglon"></td>
        <td class="num kilos-renglon"></td>
        <td><button type="button" class="boton chico secundario" title="Quitar">✕</button></td>`;
      const selProd = tr.querySelector('select');
      const cant = tr.querySelector(`input[name="${campo('cantidad')}"]`);
      const conv = tr.querySelector('.conv');
      const info = tr.querySelector('.info-prod');
      const celdaPallets = tr.querySelector('.pallets-renglon');
      const celdaKilos = tr.querySelector('.kilos-renglon');

      function actualizarRenglon() {
        // Debajo de la cantidad: en qué unidad se cuenta. Al costado: en cuántos pallets va (el incompleto
        // también es un pallet: 80 bolsas con 48 por pallet = 2 pallets) y los kilos.
        const p = productoDe(selProd.value);
        const t = leerNumero(cant.value);
        conv.textContent = p ? 'en ' + (p.unidad || '').toLowerCase() : '';
        const c = p ? contarPallets(t, Number(p.por_pallet)) : null;
        if (!p) celdaPallets.textContent = '';
        else if (!p.por_pallet) celdaPallets.innerHTML = '<span class="ayuda" title="Cargá las unidades por pallet en el producto">sin unidades por pallet</span>';
        else if (!c) celdaPallets.textContent = '—';
        else celdaPallets.innerHTML = `<strong>${c.total} ${palabraPallets(c.total)}</strong>` +
          (c.resto ? `<div class="ayuda">${esc(detallePallets(c, p.unidad))}</div>` : '');
        if (!p) celdaKilos.textContent = '';
        else if (!p.kilos) { celdaKilos.innerHTML = '<span class="ayuda" title="Cargá los kilos por unidad en el producto">sin kilos</span>'; }
        else celdaKilos.innerHTML = (t > 0 ? '<strong>' + fmt(r3(t * p.kilos)) + ' kg</strong>' : '—');
        actualizarTotales();
      }
      function prepararProducto() {
        // Los datos del producto, debajo del nombre.
        const p = productoDe(selProd.value);
        if (!p) info.textContent = '';
        else {
          const u = (p.unidad || '').toLowerCase();
          info.textContent = (p.por_pallet ? `${fmt(p.por_pallet)} ${u} por pallet` : 'sin unidades por pallet') + ' · ' +
                             (p.kilos ? `${fmt(p.kilos)} kg por unidad` : 'sin kilos por unidad');
        }
        actualizarRenglon();
      }
      cant.addEventListener('input', actualizarRenglon);
      selProd.addEventListener('change', () => {
        if (esEgreso) tr.querySelector(`select[name="${campo('lote_id')}"]`).innerHTML = opcionesLote(selProd.value);
        prepararProducto();
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
      prepararProducto();
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

  return {fmt, fmtCampo, r3, dmy, esc, leerNumero, contarPallets, textoPallets, transporte, tabla, insumosCliente, envasesCliente, fotos};
})();
