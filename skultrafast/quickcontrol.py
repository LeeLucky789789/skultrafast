"""
Module to import and work with files generated by QuickControl from phasetech.
"""
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sized, Union

import attr
import numpy as np
from scipy.constants import speed_of_light

from skultrafast.dataset import PolTRSpec, TimeResSpec
from skultrafast.twoD_dataset import TwoDim


def parse_str(s: str):
    """
    Parse entry of info file

    Parameters
    ----------
    s : str
        Value
    Returns
    -------
    obj
        Corresponding python type
    """
    if s.isnumeric():
        return int(s)
    elif set(s) - set('-.0123456789E') == set():
        # Try is a workaround for the version string
        try:
            return float(s)
        except ValueError:
            return s
    elif set(s) - set('-.0123456789E,') == set():
        return list(map(float, s.split(',')))
    elif s == 'TRUE':
        return True
    elif s == 'FALSE':
        return False
    else:
        return s


def bg_correct(wavelengths, data, left=30, right=30, deg=1):
    """
    Fit and subtract baseline from given data

    Parameters
    ----------
    wavelengths : np.ndarry
        Shared x-values
    data : np.ndarray
        Dataarray
    left : int, optional
        left points to use, by default 30
    right : int, optional
        right points to use, by default 30
    deg : int, optional
        Degree of the polynomial fit, by default 1 (linear)

    Returns
    -------
    [type]
        [description]
    """
    x = np.hstack((wavelengths[:left], wavelengths[-right:]))
    y = np.hstack((data[:, :left], data[:, -right:]))
    coef = np.polynomial.polynomial.polyfit(x, y.T, deg=deg)
    back = np.polynomial.polynomial.polyval(wavelengths, coef)
    data -= back
    return data


@attr.s(auto_attribs=True)
class QCFile:
    """
    Base class for QC files.
    """
    fname: str = attr.ib()
    """Full path to info file"""

    path: Path = attr.ib()
    """Directory of the info file"""

    prefix: str = attr.ib()
    """Filename"""

    info: dict = attr.ib()
    """Content of the info file"""
    @path.default
    def _path(self):
        return Path(self.fname).parent

    @prefix.default
    def _prefix(self):
        return Path(self.fname).with_suffix('').name

    @info.default
    def _load_info(self):
        h = []
        d = {}
        with (self.path / self.prefix).with_suffix('.info').open() as i:
            for l in i:
                key, val = l.split('\t')
                val = val[:-1].strip()
                d[key] = parse_str(val)
        return d


@attr.s(auto_attribs=True)
class QCTimeRes(QCFile):
    wavelength: np.ndarray = attr.ib()

    @wavelength.default
    def calc_wl(self, disp=None):
        if disp is None:
            grating = self.info['MONO1 Grating']
            disp_per_grating = {'30': 7.7, '75': 7.7 * 30 / 75.}
            disp = disp_per_grating[grating.split()[2]]
        wls = (np.arange(128) - 64) * disp + self.info['MONO1 Wavelength']
        self.wavelength = wls
        return wls

    @property
    def wavenumbers(self):
        return 1e7 / self.wavelength


@attr.s(auto_attribs=True)
class QC1DSpec(QCTimeRes):
    t: Iterable[float] = attr.ib()
    par_data: np.ndarray = attr.ib()
    per_data: np.ndarray = attr.ib()

    @par_data.default
    def _load_par(self):
        par_scan_files = self.path.glob(self.prefix + '*_PAR*.scan')
        return np.array([np.loadtxt(p)[:-1, 1:] for p in par_scan_files])

    @per_data.default
    def _load_per(self):
        per_scan_files = self.path.glob(self.prefix + '*_PER*.scan')
        return np.array([np.loadtxt(p)[:-1, 1:] for p in per_scan_files])

    @t.default
    def _t_default(self):
        t_list = self.info['Delays']
        return np.array(t_list) / 1000.

    def make_pol_ds(self, sigma=None) -> PolTRSpec:
        para = np.nanmean(self.par_data, axis=0)
        ds_para = TimeResSpec(self.wavelength, self.t, 1000 * para, disp_freq_unit='cm')
        perp = np.nanmean(self.per_data, axis=0)
        ds_perp = TimeResSpec(self.wavelength, self.t, 1000 * perp, disp_freq_unit='cm')
        return PolTRSpec(ds_para, ds_perp)


@attr.s(auto_attribs=True)
class QC2DSpec(QCTimeRes):
    t: Sized = attr.ib()
    t1: np.ndarray = attr.ib()
    par_data: Dict = attr.ib()
    per_data: Dict = attr.ib()
    per_spec: Optional[Dict] = None
    par_spec: Optional[Dict] = None
    upsampling: int = 2
    pump_freq: np.ndarray = attr.ib()

    @t.default
    def _t_default(self):
        t_list = self.info['Waiting Time Delays']
        return np.array(t_list) / 1000.

    @t1.default
    def _load_t1(self):
        end = self.info['Final Delay (fs)']
        step = self.info['Step Size (fs)']
        return np.arange(0.0, end+1, step)/1000.

    def _loader(self, which: str):
        data_dict: Dict[int, np.ndarray] = {}

        for t in range(len(self.t)):
            T = '_T%02d' % (t+1)
            par_scans = self.path.glob(self.prefix + T + f'_{which}*.scan')
            data = []
            for s in par_scans:
                d = np.loadtxt(s)
                self.t2 = d[1:, 0]
                data.append(d)
            if len(data)>0:
                d = np.array(data)
                data_dict[t] = d
            else:
                self.t = self.t[:t+1]
                break
        return data_dict

    def calc_spec(self):
        spec_dict: Dict[int, np.ndarray] = {}
        for t in self.par_data:
            d = np.nanmean(self.par_data[t], 0)
            print(t, d.shape)
            d = d[:-1, 1:]
            d[0, :] *= 0.5
            win = np.hamming(self.upsampling * len(self.t1))
            spec = np.fft.rfft(d * win[len(self.t1):, None],
                               axis=0,
                               n=self.upsampling * len(self.t1))
            spec_dict[t] = spec.real
        return spec_dict

    @par_data.default
    def _load_par(self):
        return self._loader('PAR')

    @per_data.default
    def _load_per(self):
        return self._loader('PER')

    @pump_freq.default
    def _calc_freqs(self):
        freqs = np.fft.rfftfreq(self.upsampling * len(self.t1), self.t1[1] - self.t1[0])
        om0 = self.info['Rotating Frame (Scanned)']
        cm = 0.01 / ((1/freqs) * 1e-12 * speed_of_light) + om0
        return cm

    def make_ds(self):
        par_arr = np.dstack(list(self.calc_spec().values())).T
        return TwoDim(t=self.t, pump_wn=self.pump_freq, probe_wn=self.wavenumbers,
                      spec2d=par_arr)
