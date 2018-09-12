import numpy as np
import astropy.stats as stats
from skultrafast.data_io import save_txt
from skultrafast.filter import bin_channels
from enum import Enum

class Polarization(Enum):
    """Enum which describes the relative polarisation of pump and probe"""
    MAGIC = 'magic'
    PARA = 'para'
    PERP = 'perp'
    CIRC = 'circ'
    UNKNOWN = 'unknown'


class MesspyDataSet:
    def __init__(self, fname, is_pol_resolved=False, 
                 pol_first_scan='unknown', valid_channel='both'):
        """Class for working with data files from MessPy.
        
        Parameters
        ---------- 
        fname : str
            Filename to open.
        is_pol_resolved : bool (false)
            If the dataset was recorded polarization resolved.
        pol_first_scan : {'magic', 'para', 'perp', 'unknown'}
            Polarization between the pump and the probe in the first scan. 
            If `valid_channel` is 'both', this corresponds to the zeroth channel. 
        valid_channel : {0, 1, 'both'}
            Indicates which channels contains a real signal. 

        """

        with np.load(fname) as f:
            self.wl = f['wl']
            self.t = f['t']
            self.data = f['data']

        self.pol_first_scan = pol_first_scan
        self.is_pol_resolved = is_pol_resolved
        self.valid_channel = valid_channel

    def average_scans(self, sigma=3):
        """Calculate the average of the scans. Uses sigma clipping, which 
        also filters nans. For polarization resovled measurements, the 
        function assumes that the polarisation switches every scan.

        Parameters
        ----------
        sigma : float
           sigma used for sigma clipping.

        Returns
        -------
        : dict or DataSet
            DataSet or Dict of DataSets containing the averaged datasets. 

        """        

        num_wls = self.data.shape[0]

        if not self.is_pol_resolved:
            data = stats.sigma_clip(self.data, 
                                    sigma=sigma, axis=-1)
            data = data.mean(-1)
            std = data.std(-1)
            err = std/np.sqrt(std.mask.sum(-1))
            
            if self.valid_channel in [0, 1]:
                data = data[..., self.valid_channel]
                std = std[..., self.valid_channel]
                err = err[..., self.valid_channel]

                out = {}
                
                if num_wls > 1:
                    for i in range(num_wls):                     
                        ds = DataSet(self.wl[:, i], self.t, data[i, ...], err[i, ...])
                        out[self.pol_first_scan + str(i)] = ds
                else:
                    out = DataSet(self.wl[:, 0], self.t, data[0, ...], err[0, ...])
                return out
            
        elif self.is_pol_resolved and self.valid_channel in [0, 1]:
            assert(self.pol_first_scan in ['para', 'perp'])
            data1 = stats.sigma_clip(self.data[..., self.valid_channel,::2], 
                                    sigma=sigma, axis=-1)
            data1 = data1.mean(-1)
            std1 = data1.std(-1)
            err1 = std1/np.sqrt(data1.mask.sum(-1))
            
            data2 = stats.sigma_clip(self.data[..., self.valid_channel, 1::2], 
                                    sigma=sigma, axis=-1)
            data2 = data2.mean(-1)
            std2 = data2.std(-1)
            err2 = std2/np.sqrt(data2.mask.sum(-1))
        
            out = {}
            for i in range(self.data.shape[0]):
                out[self.pol_first_scan + str(i)] = DataSet(self.wl[:, i], self.t, data1[i, ...], err1[i, ...])
                other_pol = 'para' if self.pol_first_scan == 'perp' else 'perp'
                out[other_pol + str(i)] = DataSet(self.wl[:, i], self.t, data2[i, ...], err2[i, ...])
                iso = 1/3*out['para' + str(i)].data + 2/3*out['perp' + str(i)].data
                iso_err = np.sqrt(1/3*out['para' + str(i)].err**2 + 2/3*out['perp' + str(i)].data**2)
                out['iso' + str(i)] = DataSet(self.wl[:, i], self.t, iso, iso_err)
            return out
        else:
            raise NotImplementedError("Iso correction not suppeorted yet.")


class DataSet:
    def __init__(self, wl, t, data, err=None, freq_unit='nm'):
        """Class for containing a 2D spectra.
        
        Parameters
        ----------
        wl : array of shape(n)
            Array of the spectral dimension
        t : array of shape(m)
            Array with the delay times.
        data : array of shape(n, m)
            Array with the data for each point.
        err : array of shape(n, m) (optional)
            Contains the std err  of the data.
        name : str
            Identifier for data set. (optionl)
        freq_unit : {'nm', 'cm'} 
            Unit of the wavelength array, default is 'nm'.
        """

        assert((wl.shape[0], t.shape[0]) == data.shape)

        if freq_unit == 'nm':
            self.wavelengths = wl
            self.wavenumbers = 1e7/wl
            self.wl = self.wavelengths
        else:
            self.wavelengths = 1e7/wl
            self.wavenumbers = wl
            self.wl = self.wavenumbers

        self.t = t
        self.data = data
        if err is not None:
            self.err = err
        idx = np.argsort(self.wavelengths)
        self.wavelengths = self.wavelengths[idx]
        self.wavenumbers = self.wavenumbers[idx]
        

    def __iter__(self):
        """For compatbility with dv.tup"""
        return iter(self.wavelengths, self.t, self.data)

    def save_txt(self, fname, freq_unit='wl'):
        """Save the dataset as a text file
        
        Parameters
        ----------
        fname : str
            Filename (can include filepath)

        freq_unit : 'nm' or 'cm' (default 'nm')
            Which frequency unit is used.
        """
        wl = self.wavelengths if freq_unit is 'wl' else self.wavenumbers
        save_txt(fname, wl, self.t, self.data)

    def cut_freqs(self, freq_ranges=None, freq_unit='nm'):
        """Remove channels outside of given frequency ranges.

        Parameters
        ----------
        freq_ranges : list of (float, float)
            List containing the edges (lower, upper) of the
            frequencies to keep.
        freq_unit : {'nm', 'cm'}
            Unit of the given edges.
        
        Returns
        -------
        : DataSet
            DataSet containing only the listed regions.
        """
        idx = np.zeros_like(self.wavelengths, dtype=np.bool)
        arr =  self.wavelengths if freq_unit is 'nm' else self.wavenumbers
        for (lower, upper) in freq_ranges:           
                idx ^= np.logical_and(arr > lower, arr < upper)
        if self.err is not None:
            err = self.err[:, idx]
        else:
            err = None
        return DataSet(arr[idx], self.t, self.data[:, idx], err, freq_unit)

    def cut_times(self, time_ranges):
        """Remove spectra outside of given time-ranges.

        Parameters
        ----------
        time_ranges : list of (float, float)
            List containing the edges of the time-regions to keep.
        
        Returns
        -------
        : DataSet 
            DataSet containing only the requested regions.
        """
        idx = np.zeros_like(self.t, dtype=np.bool)
        arr = self.t
        for (lower, upper) in time_ranges:           
                idx ^= np.logical_and(arr > lower, arr < upper)
        if self.err is not None:
            err = self.err[idx, :]
        else:
            err = None
        return DataSet(self.wavelengths, self.t[idx], self.data[idx, :], err)

    def subtract_background(self, n : int=10):
        """Subtracts the first n-spectra from the dataset"""
        self.data -= np.mean(self.data[:n, :], 0, keepdims=1)

    def bin_freqs(self, n : int, freq_unit='nm'):
        """Bins down the dataset by averaging over spectral channel.

        Parameters
        ----------
        n : int
            The number of bins. The edges are calculated by
            np.linspace(freq.min(), freq.max(), n+1).
        freq_unit : {'nm', 'cm'}
            Whether to calculate the bin-borders in
            frequency- of wavelengt-space.
        """
        arr =  self.wavelengths if freq_unit is 'nm' else self.wavenumbers
        # Slightly offset edges to include themselves.
        edges = np.linspace(arr.min()-0.002, arr.max()+0.002, n+1)
        np.seachsorted()
    pass


class DataSetPlotter:
    def __init__(self, dataset):
        self.dataset = Da
        
        




           




