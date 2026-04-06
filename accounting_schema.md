Analiza todas las imagenes dentro del directorio `examples/`. Omite cualquier archivo cuyo nombre ya termine en `-processed` (por ejemplo `factura-123-processed.png`).

Para cada imagen restante:
1. Ejecuta OCR y cualquier tecnica necesaria para capturar los campos del comprobante descritos en el README del proyecto.
2. Cada item contable que pueda identificarse en el ticket/factura debe generarse como una fila individual en un CSV, usando las siguientes columnas exactas: Clase, Comprobante, Fecha, F.Emision, Nombre, Cuit, Articulo, Detalle, Cuenta, Precio, IVA, Centro Costo, Tipo Comp., Afecta Iva, Percep 1, Importe Percep 1, Percep 2, Importe Percep 2, Percep 3, Importe Percep 3, Iva Total, Cantidad.
   - Si un campo no aplica, deja la celda vacia.
   - Para `Cantidad`, si no se encuentra explicitamente, usa el valor por defecto `1`.
   - `Clase` corresponde al tipo de comprobante (factura X, monotributo, etc.); `Comprobante` es el numero de factura/ticket; `Fecha` es la fecha de carga; `F.Emision` la fecha de emision del comprobante; `Detalle` proviene de la descripcion del gasto; `Cuenta` depende del articulo; `Precio` es el importe neto; `IVA` indica si la factura incluye IVA y su importe.
3. Añade o actualiza la fila en un archivo CSV de salida (por ejemplo `outputs/procesados.csv`). Si el archivo no existe, crea encabezados con el orden indicado.

Cuando termines de procesar cada imagen, renombra el archivo original agregando `-processed` antes de la extension (por ejemplo `factura-123.png` ⇒ `factura-123-processed.png`) para evitar reprocesarlo en ejecuciones posteriores.
