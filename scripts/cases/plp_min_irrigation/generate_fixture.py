"""Generate the synthetic plp_min_irrigation PLP case (12 monthly stages, 2 hydrologies)."""

import sys
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
# PLP files (plpeta/plpblo/plpdem/plpaflce) use HYDROLOGICAL months:
# 1 = April .. 12 = March.  CAL maps them to calendar months (hours).
MONTHS = list(range(1, 13))
CAL = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
YEARS = [1] * 12  # one hydrological year (April..March)
HOURS = {
    1: 744,
    2: 672,
    3: 744,
    4: 720,
    5: 744,
    6: 720,
    7: 744,
    8: 744,
    9: 720,
    10: 744,
    11: 720,
    12: 744,
}
NS = 12


def w(name, text):
    (out / name).write_text(text)


eta = (
    "# Archivo con la duracion de las etapas\n# Etapas\n     %d   'H'\n# Ano  Mes  Etapa FDesh   NHoras    FactTasa    TipoEtapa\n"
    % NS
)
for i, (y, m) in enumerate(zip(YEARS, MONTHS), 1):
    eta += "  %03d  %03d    %03d     F      %03d    1.000000    '1 Bloque'\n" % (
        y,
        m,
        i,
        HOURS[CAL[m - 1]],
    )
w("plpeta.dat", eta)
blo = (
    "# Archivo con la duracion de los bloques\n# Bloques\n     %d\n# Bloque   Etapa   NHoras  Ano   Mes  TipoBloque\n"
    % NS
)
for i, (y, m) in enumerate(zip(YEARS, MONTHS), 1):
    blo += "    %03d    %03d      %03d    %03d    %03d    'Bloque 01'\n" % (
        i,
        i,
        HOURS[CAL[m - 1]],
        y,
        m,
    )
w("plpblo.dat", blo)
w(
    "plpbar.dat",
    "# Archivo con definicion de Barras (plpbar.dat)\n# Numero de Barras\n     1\n# Numero       Nombre\n       1       'Bus1'\n",
)
w(
    "plpcnfli.dat",
    "# Archivo de configuracion de lineas (plpcnfli.dat)\n# Num.Lineas   Modela Perdidas  Perd.en.ERM   Ang. de Ref.\n         0          F             'M'         1000.d0\n# Nombre   F.Max.A-B  F.Max.B-A  Barra A  Barra B  Tension  R(Ohm)  X(ohm)  Mod.Perd.  Num.Tramos  Operativa\n",
)
dem = (
    "# Archivo de demandas por barra (plpdem.dat)\n# Numero de barras\n1\n# Nombre de la Barra\n'Bus1'\n# Numero de Demandas\n%d\n# Mes  Bloque   Demanda\n"
    % NS
)
for i, m in enumerate(MONTHS, 1):
    dem += "   %02d    %03d    900.0\n" % (m, i)
w("plpdem.dat", dem)
for f, t in [
    (
        "plpcosce.dat",
        "# Archivo de precios de termicas (plpcosce.dat)\n# Numero de centrales termicas con cambio de costo variable\n 0\n",
    ),
    (
        "plpmance.dat",
        "# Archivo de mantenimientos de centrales (plpmance.dat)\n# numero de centrales con mantenimientos\n 0\n",
    ),
    (
        "plpmanem.dat",
        "# Archivo de mantenimientos embalses (plpmanem.dat)\n# Numero de embalses con mantenimientos\n 0\n",
    ),
    (
        "plpmanli.dat",
        "# Archivo de mantenimientos de lineas (plpmanli.dat)\n# numero de lineas con mantenimientos\n 0\n",
    ),
]:
    w(f, t)
w(
    "plpextrac.dat",
    "# Archivo de Extracciones (plpextrac.dat)\n# Numero Centrales con extraccion\n2\n"
    + "".join(
        "# Nombre de central de extraccion\n'COLBUN'\n# Maxima Extraccion (m3/seg)\n%.1f\n# Nombre de la Central aguas abajo\n'CHIBURGO'\n"
        % q
        for q in (24.0, 50.0)
    ),
)

HDR = "                                                  IPot MinTec  Inter   FCAD    Cen_MTTdHrz\n"


def emb(num, name, pmax, rendi, bus, gen, ver, afl, vini, vfin, vmin, vmax):
    return (
        HDR
        + "   %2d %-50s1    F       F       F       F\n" % (num, "'%s'" % name)
        + "          PotMin PotMax VertMin VertMax\n             0.0 %6.1f   000.0   000.0\n"
        % pmax
        + "           Start   Stop ON(t<0) NEta_OnOff\n             0.0    0.0 F       0               Pot.           Volumen    Volumen    Volumen    Volumen  Factor\n"
        "          CosVar  Rendi  Barra Genera Vertim    t<0  Afluen    Inicial      Final     Minimo     Maximo  Escala EmbCFUE\n"
        "             0.0  %5.3f    %3d    %3d    %3d    0.0  %06.1f  %9.7f  %9.7f  %9.7f  %9.7f  1.0E+9       T\n"
        # Vfin = Vmin: no hard terminal target (EmbVFin is a hard bound
        # in PLP volfinem.f; with no boundary cuts it only over-constrains).
         % (rendi, bus, gen, ver, afl, vini, vmin, vmin, vmax)
    )


def ser(num, name, pmax, rendi, bus, sh, sv, afl, cos=0.0):
    return (
        HDR
        + "   %2d %-50s1    F       F       F       F\n" % (num, "'%s'" % name)
        + "          PotMin PotMax\n             0.0 %6.1f\n" % pmax
        + "           Start   Stop ON(t<0) NEta_OnOff\n             0.0    0.0 F       0\n"
        "          CosVar  Rendi  Barra SerHid SerVer    t<0  Afluen\n"
        "          %6.1f  %5.3f    %3d    %3d    %3d    0.0  %06.1f\n"
        % (cos, rendi, bus, sh, sv, afl)
    )


c = "# Archivo de configuracion de las centrales (plpcnfce.dat)\n# Num.Centrales  Num.Embalses Num.Serie Num.Fallas Num.Pas.Pur. Num.BAT\n"
c += "    21            5          14         1          0            0\n# Interm Min.Tec. Cos.Arr.Det. FFaseSinMT EtapaCambioFase\n  F      F        F            F          00\n# Centrales de Embalse\n"
c += emb(
    1, "ELTORO", 450.0, 2.000, 1, 9, 9, 60.0, 3.0000000, 2.5000000, 0.0000000, 5.6000000
)
c += emb(
    2, "LMAULE", 0.0, 1.000, 0, 6, 6, 20.0, 0.4000000, 0.4000000, 0.0000000, 1.4500000
)
c += emb(
    3,
    "CIPRESES",
    105.0,
    2.784,
    1,
    5,
    5,
    15.0,
    0.1000000,
    0.1000000,
    0.0000000,
    0.1750000,
)
c += emb(
    4,
    "PEHUENCHE",
    500.0,
    1.780,
    1,
    5,
    5,
    30.0,
    0.2000000,
    0.2000000,
    0.0500000,
    0.4000000,
)
c += emb(
    5,
    "COLBUN",
    474.0,
    1.530,
    1,
    12,
    12,
    0.0,
    1.0000000,
    1.0000000,
    0.3800000,
    1.5500000,
)
c += "# Centrales Pasada Serie\n"
c += ser(6, "ARMERILLO", 0.0, 0.0, 0, 5, 5, 0.0)
c += ser(7, "B_LaMina", 0.0, 0.0, 0, 5, 5, 10.0)
c += ser(8, "B_Maule", 0.0, 0.0, 0, 5, 5, 8.0)
# Laja districts: consumptive (SerHid 0), the spill path chains onward
c += ser(9, "RIEGZACO", 9999.0, 1.0, 0, 0, 10, 0.0)
c += ser(10, "RieTucapel", 9999.0, 1.0, 0, 0, 11, 0.0)
c += ser(11, "RieSaltos", 9999.0, 1.0, 0, 0, 0, 0.0)
md = [
    "RieCMNA",
    "RieCMNB",
    "RieMaitenes",
    "RieMauleSur",
    "RieMelado",
    "RieSur123SCDZ",
    "RieMolinosOtros",
]
for k, n in enumerate(md):
    nxt = 12 + k + 1 if k < len(md) - 1 else 0
    c += ser(12 + k, n, 9999.0, 1.0, 0, 0, nxt, 0.0)
c += ser(21, "CHIBURGO", 0.0, 0.0, 0, 12, 12, 0.0)
c += "# Centrales Pasada Pura\n# Centrales Termicas y de Falla\n"
c += ser(19, "Thermal1", 1000.0, 1.0, 1, 0, 0, 0.0, cos=100.0)
c += ser(20, "Falla1", 9999.0, 1.0, 1, 0, 0, 0.0, cos=1500.0)
w("plpcnfce.dat", c)
# inflows: 2 hydrologies (wet/dry)
infl = {
    "ELTORO": (60, 30),
    "LMAULE": (25, 8),
    "CIPRESES": (18, 8),
    "PEHUENCHE": (40, 15),
    "B_LaMina": (12, 5),
    "B_Maule": (10, 4),
}
season = [0.8, 1.0, 1.3, 1.4, 1.3, 1.2, 1.4, 1.6, 1.3, 0.9, 0.7, 0.7]
a = (
    "# Archivo de caudales por etapa\n# Nro. Cent. c/Caudales Estoc. (EstocNVar2) y Nro. Hidrologias (NClase)\n  %d                                         2\n"
    % len(infl)
)
for name, (wet, dry) in infl.items():
    a += (
        "# Nombre de la central\n'%s'\n#   Numero de bloques con caudales\n%d\n# Mes   Bloque    Caudal\n"
        % (name, NS)
    )
    for i, m in enumerate(MONTHS, 1):
        a += "  %03d     %03d  %7.2f  %7.2f\n" % (
            m,
            i,
            wet * season[i - 1],
            dry * season[i - 1],
        )
w("plpaflce.dat", a)
