// Achica las fotos en el navegador antes de mandarlas: las del celular pesan varios MB cada una.
// Si algo falla (navegador viejo, formato HEIC), se mandan como están.
window.prepararFotos = function (input, form, avisar) {
  const LADO_MAXIMO = 1600;
  let preparando = false;
  avisar = avisar || function () {};

  function cargarImagen(archivo) {
    return new Promise((ok, mal) => {
      const url = URL.createObjectURL(archivo);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); ok(img); };
      img.onerror = () => { URL.revokeObjectURL(url); mal(new Error('no se pudo leer la imagen')); };
      img.src = url;
    });
  }

  async function achicar(archivo) {
    if (!/^image\/(jpeg|png|webp)$/.test(archivo.type)) return archivo;
    try {
      const img = await cargarImagen(archivo);
      const escala = Math.min(1, LADO_MAXIMO / Math.max(img.naturalWidth, img.naturalHeight));
      if (escala === 1 && archivo.size < 1500000) return archivo;
      const lienzo = document.createElement('canvas');
      lienzo.width = Math.round(img.naturalWidth * escala);
      lienzo.height = Math.round(img.naturalHeight * escala);
      const ctx = lienzo.getContext('2d');
      ctx.fillStyle = '#fff';  // fondo blanco para las PNG con transparencia
      ctx.fillRect(0, 0, lienzo.width, lienzo.height);
      ctx.drawImage(img, 0, 0, lienzo.width, lienzo.height);
      const blob = await new Promise(ok => lienzo.toBlob(ok, 'image/jpeg', 0.82));
      if (!blob || blob.size >= archivo.size) return archivo;
      return new File([blob], archivo.name.replace(/\.[^.]+$/, '') + '.jpg', {type: 'image/jpeg'});
    } catch (e) {
      return archivo;
    }
  }

  input.addEventListener('change', async () => {
    const elegidas = Array.from(input.files);
    if (!elegidas.length) { avisar([]); return; }
    preparando = true;
    avisar(null);  // null: todavía se están preparando
    const listas = [];
    for (const a of elegidas) listas.push(await achicar(a));
    let finales = listas;
    try {
      const dt = new DataTransfer();
      listas.forEach(a => dt.items.add(a));
      input.files = dt.files;
    } catch (e) {
      finales = elegidas;
    }
    preparando = false;
    avisar(finales);
  });

  form.addEventListener('submit', ev => {
    if (preparando) { ev.preventDefault(); avisar(null); }
  });
};
