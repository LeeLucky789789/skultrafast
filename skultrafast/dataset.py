from collections import namedtuple

import matplotlib.pyplot as plt
import numpy as np
from numpy.core.multiarray import ndarray
from astropy.stats import sigma_clip

import skultrafast.dv as dv
import skultrafast.plot_helpers as ph
from skultrafast import zero_finding, fitter
from skultrafast.data_io import save_txt
from skultrafast.filter import uniform_filter, svd_filter

EstDispResult = namedtuple('EstDispResult', 'correct_ds tn polynomial')
EstDispResult.__doc__ = """
Tuple containing the results from an dispersion estimation.

Attributes
----------
correct_ds : DataSet
    A dataset were we used linear interpolation to remove the dispersion.
tn : array
    Array containing the results of the applied heuristic. 
polynomial : function
    Function which maps wavenumbers to time-zeros.
"""

FitExpResult = namedtuple('FitExpResult', 'lmfit_mini lmfit_res fitter')


class DataSet:
    def __init__(self, wl, t, data, err=None, name=None, freq_unit='nm',
                 disp_freq_unit=None, auto_plot=True):
        """
        Class for working with time-resolved spectra. If offers methods for
        analyzing and pre-processing the data. To visualize the data,
        each `DataSet` object has an instance of an `DataSetPlotter` object
        accessible under `plot`.

        Parameters
        ----------
        wl : array of shape(n)
            Array of the spectral dimension
        t : array of shape(m)
            Array with the delay times.
        data : array of shape(n, m)
            Array with the data for each point.
        err : array of shape(n, m) or None (optional)
            Contains the std err of the data, can be `None`.
        name : str (optional)
            Identifier for data set.
        freq_unit : 'nm' or 'cm' (optional)
            Unit of the wavelength array, default is 'nm'.
        disp_freq_unit : 'nm','cm' or None (optional)
            Unit which is used by default for plotting. If `None`, it defaults
            to `freq_unit`.

        Attributes
        ----------
        wavelengths, wavenumbers, t, data : ndarray
            Arrays with the data itself.
        plot : DataSetPlotter
            Helper class which can plot the dataset using `matplotlib`.
        t_idx : function
            Helper function to find the nearest index in t for a given time.
        wl_idx : function
            Helper function to search for the nearest wavelength index for a
            given wavelength.
        wn_idx : function
            Helper function to search for the nearest wavelength index for a
            given wavelength.
        """

        assert ((t.shape[0], wl.shape[0]) == data.shape)

        if freq_unit == 'nm':
            self.wavelengths = wl
            self.wavenumbers = 1e7 / wl
            self.wl = self.wavelengths
        else:
            self.wavelengths = 1e7 / wl
            self.wavenumbers = wl
            self.wl = self.wavenumbers

        self.t = t
        self.data = data
        self.err = err

        if name is not None:
            self.name = name

        # Sort wavelenths and data.
        idx = np.argsort(self.wavelengths)
        self.wavelengths = self.wavelengths[idx]
        self.wavenumbers = self.wavenumbers[idx]
        self.data = self.data[:, idx]
        self.auto_plot = True
        self.plot = DataSetPlotter(self)
        self.t_idx = dv.make_fi(self.t)
        self.wl_idx = dv.make_fi(self.wavelengths)
        self.wn_idx = dv.make_fi(self.wavenumbers)

        if disp_freq_unit is None:
            self.disp_freq_unit = freq_unit
        else:
            self.disp_freq_unit = disp_freq_unit
        self.plot.freq_unit = self.disp_freq_unit

    def __iter__(self):
        """For compatbility with dv.tup"""
        return iter((self.wavelengths, self.t, self.data))

    @classmethod
    def from_txt(cls, fname, freq_unit='nm', time_div=1., loadtxt_kws=None):
        """
        Directly create a dataset from a text file.

        Parameters
        ----------
        fname : str
            Name of the file. This function assumes the data is given by a
            (n+1, m+1) table. Excludig the [0, 0] value, the first row gives the
            frequencies and the first column gives the delay-times.
        freq_unit : {'nm', 'cm'}
            Unit of the frequencies.
        time_div : float
            Since `skultrafast` prefers to work with picoseconds and programs
            may use different units, it divides the time-values by `time_div`.
            Use `1`, the default, to not change the time values.
        loadtxt_kws : dict
            Dict containing keyword arguments to `np.loadtxt`.
        """
        if loadtxt_kws is None:
            loadtxt_kws = {}
        tmp = np.loadtxt(fname, **loadtxt_kws)
        t = tmp[1:, 0] / time_div
        freq = tmp[0, 1:]
        data = tmp[1:, 1:]
        return cls(freq, t, data, freq_unit=freq_unit)

    def save_txt(self, fname, freq_unit='wl'):
        """
        Saves the dataset as a text file.

        Parameters
        ----------
        fname : str
            Filename (can include path)

        freq_unit : 'nm' or 'cm' (default 'nm')
            Which frequency unit is used.
        """
        wl = self.wavelengths if freq_unit is 'wl' else self.wavenumbers
        save_txt(fname, wl, self.t, self.data)

    def cut_freqs(self, freq_ranges=None, invert_sel=False, freq_unit='nm'):
        """
        Removes channels inside (or outside ) of given frequency ranges.

        Parameters
        ----------
        freq_ranges : list of (float, float)
            List containing the edges (lower, upper) of the
            frequencies to keep.
        invert_sel : bool
            Invert the final selection.
        freq_unit : {'nm', 'cm'}
            Unit of the given edges.

        Returns
        -------
        : DataSet
            DataSet containing only the listed regions.
        """
        idx = np.zeros_like(self.wavelengths, dtype=np.bool)
        arr = self.wavelengths if freq_unit is 'nm' else self.wavenumbers
        for (lower, upper) in freq_ranges:
            idx ^= np.logical_and(arr > lower, arr < upper)
        if not invert_sel:
            idx = ~idx
        if self.err is not None:
            err = self.err[:, idx]
        else:
            err = None
        return DataSet(arr[idx], self.t, self.data[:, idx], err, freq_unit)

    def mask_freqs(self, freq_ranges=None, invert_sel=False, freq_unit='nm'):
        """
        Mask channels inside of given frequency ranges.

        Parameters
        ----------
        freq_ranges : list of (float, float)
            List containing the edges (lower, upper) of the
            frequencies to keep.
        invert_sel : bool
            When True, it inverts the selection. Can be used
            mark everything outside selected ranges.
        freq_unit : {'nm', 'cm'}
            Unit of the given edges.

        Returns
        -------
        : DataSet
            DataSet containing only the listed regions.
        """
        idx = np.zeros_like(self.wavelengths, dtype=np.bool)
        arr = self.wavelengths if freq_unit is 'nm' else self.wavenumbers

        for (lower, upper) in freq_ranges:
            idx ^= np.logical_and(arr > lower, arr < upper)
        if not invert_sel:
            idx = ~idx
        if self.err is not None:
            self.err.mask[:, idx] = True
        self.data = np.ma.MaskedArray(self.data)
        self.data[:, idx] = np.ma.masked
        # self.wavelengths = np.ma.MaskedArray(self.wavelengths, idx)
        # self.wavenumbers = np.ma.MaskedArray(self.wavenumbers, idx)

    def cut_times(self, time_ranges, invert_sel=False):
        """
        Remove spectra inside (or outside) of given time-ranges.

        Parameters
        ----------
        time_ranges : list of (float, float)
            List containing the edges of the time-regions to keep.
        invert_sel : bool
            Inverts the final selection.
        Returns
        -------
        : DataSet
            DataSet containing only the requested regions.
        """
        idx = np.zeros_like(self.t, dtype=np.bool)
        arr = self.t
        for (lower, upper) in time_ranges:
            idx ^= np.logical_and(arr > lower, arr < upper)
        if not invert_sel:
            idx = ~idx
        if self.err is not None:
            err = self.err[idx, :]
        else:
            err = None
        return DataSet(self.wavelengths, self.t[idx], self.data[idx, :], err)

    def mask_times(self, time_ranges, invert_sel=False):
        """
        Mask spectra inside (or outside) of given time-ranges.

        Parameters
        ----------
        time_ranges : list of (float, float)
            List containing the edges of the time-regions to keep.
        invert_sel : bool
            Invert the selection.

        Returns
        -------
        : None
        """
        idx = np.zeros_like(self.t, dtype=np.bool)
        arr = self.t
        for (lower, upper) in time_ranges:
            idx ^= np.logical_and(arr > lower, arr < upper)
        if not invert_sel:
            idx = ~idx
        if self.err is not None:
            self.err[idx, :].mask = True
        # self.t = np.ma.MaskedArray(self.t, idx)
        self.data.mask[:, idx] = True

    def subtract_background(self, n: int = 10):
        """Subtracts the first n-spectra from the dataset"""
        self.data -= np.mean(self.data[:n, :], 0, keepdims=True)

    def bin_freqs(self, n: int, freq_unit='nm'):
        """
        Bins down the dataset by averaging over several transients.

        Parameters
        ----------
        n : int
            The number of bins. The edges are calculated by
            np.linspace(freq.min(), freq.max(), n+1).
        freq_unit : {'nm', 'cm'}
            Whether to calculate the bin-borders in
            frequency- of wavelength-space.
        Returns
        -------
        DataSet
            Binned down `DataSet`
        """
        # We use the negative of the wavenumbers to make the array sorted
        arr = self.wavelengths if freq_unit is 'nm' else -self.wavenumbers
        # Slightly offset edges to include themselves.
        edges = np.linspace(arr.min() - 0.002, arr.max() + 0.002, n + 1)
        idx = np.searchsorted(arr, edges)
        binned = np.empty((self.data.shape[0], n))
        binned_wl = np.empty(n)
        for i in range(n):
            if self.err is None:
                weights = None
            else:
                weights = 1/self.err[:, idx[i]:idx[i+1]]
            binned[:, i] = np.average(self.data[:, idx[i]:idx[i + 1]], 1,
                                      weights=weights)
            binned_wl[i] = np.mean(arr[idx[i]:idx[i + 1]])
        if freq_unit is 'cm':
            binned_wl = - binned_wl
        return DataSet(binned_wl, self.t, binned, freq_unit=freq_unit,
                       disp_freq_unit=self.disp_freq_unit)

    def bin_times(self, n, start_index=0):
        """
        Bins down the dataset by binning `n` sequential spectra together.

        Parameters
        ----------
        n : int
            How many spectra are binned together.
        start_index : int
            Determines the starting index of the binning

        Returns
        -------
        DataSet
            Binned down `DataSet`
        """

        out = []
        out_t = []
        m = len(self.t)
        for i in range(start_index, m, n):
            end_idx = min(i + n, m)
            out.append(sigma_clip(self.data[i:end_idx, :], sigma=2.5, iters=1,
                                     axis=0).mean(0))
            out_t.append(self.t[i:end_idx].mean())

        new_data = np.array(out)
        new_t = np.array(out_t)
        return DataSet(self.wavelengths, new_t, new_data,
                       disp_freq_unit=self.disp_freq_unit)

    def estimate_dispersion(self, heuristic='abs', heuristic_args=(1,), deg=2,
                            t_parameter=1.3):
        """
        Estimates the dispersion from a dataset by first
        applying a heuristic to each channel. The results are than
        robustly fitted with a polynomial of given order.

        Parameters
        ----------
        heuristic : {'abs', 'diff', 'gauss_diff'} or func
            Determines which heuristic to use on each channel. Can
            also be a function which follows `func(t, y, *args) and returns
            a `t0`-value. The heuristics are described in `zero_finding`.
        heuristic_args : tuple
            Arguments which are given to the heuristic.
        deg : int (optional)
            Degree of the polynomial used to fit the dispersion (defaults to 2).
        t_parameter : float
            Determines the robustness of the fit. See statsmodels documentation
            for more info.

        Returns
        -------
        EstDispResult
            Tuple containing the dispersion corrected version of the dataset, an
            array with time-zeros from the heuristic, and the polynomial
            function resulting from the robust fit.
        """

        if heuristic == 'abs':
            idx = zero_finding.use_first_abs(self.data, heuristic_args[0])
        else:
            raise NotImplementedError('Not done yet, sorry')

        vals, coefs = zero_finding.robust_fit_tz(self.wavenumbers, self.t[idx],
                                                 deg, t=t_parameter)
        func = np.poly1d(coefs)
        new_data = zero_finding.interpol(self, func(self.wavenumbers))
        return EstDispResult(
            correct_ds=DataSet(self.wavelengths, self.t, new_data.data),
            tn=self.t[idx], polynomial=func)

    def fit_exp(self, x0, fix_sigma=True, fix_t0=False, fix_last_decay=True,
                model_coh=True, lower_bound=0.1):
        """
        Fit a sum of exponentials to the dataset. This function assumes
        the dataset is already corrected for dispersion.

        Parameters
        ----------
        x0 : list of floats or array
            Starting values of the fit. The first value is the estimate of the
            system response time omega. If `fit_t0` is true, the second float is
            the guess of the time-zero. All other floats are interpreted as the
            guessing values for exponential decays.
        fix_sigma : bool (optional)
            If to fix the IRF duration sigma.
        fix_t0 : bool (optional)
            If to fix the the time-zero.
        fix_last_decay : bool (optional)
            Fixes the value of the last tau of the initial guess. It can be
            used to add a constant by setting the last tau to a large value
            and fix it.
        model_coh : bool (optional)
            If coherent contributions should by modeled. If `True` a gaussian
            with a width equal the system response time and its derivatives are
            added to the linear model.
        lower_bound : float (optional)
            Lower bound for decay-constants.
            :param fix_sigma:
        """

        f = fitter.Fitter(self, model_coh=model_coh, model_disp=1)
        f.res(x0)
        fixed_names = []
        if fix_sigma:
            fixed_names.append('w')

        lm_model = f.start_lmfit(x0, fix_long=fix_last_decay, fix_disp=fix_t0,
                                 lower_bound=lower_bound, full_model=False,
                                 fixed_names=fixed_names)
        ridge_alpha = abs(self.data).max() * 1e-4
        f.lsq_method = 'ridge'
        fitter.alpha = ridge_alpha
        result = lm_model.leastsq()
        return FitExpResult(lm_model, result, f)

    def lft_density_map(self, taus, alpha=1e-4, ):
        """Calculates the LDM from a dataset by regularized regression.

        Parameters
        ----------
        taus : array
            List with candiate decays.
        """
        pass


    def concat_datasets(self, other_ds):
        """
        Merge the dataset with another dataset. The other dataset need to
        have the same time axis.

        Parameters
        ----------
        other_ds : DataSet
            The dataset to merge with

        Returns
        -------
        DataSet
            The merged dataset.
        """

        all_wls = np.hstack((self.wavelengths, other_ds.wavelengths))
        all_data = np.hstack((self.data, other_ds.data))

        return DataSet(all_wls, self.t, all_data, freq_unit='nm',
                       disp_freq_unit=self.disp_freq_unit)


class DataSetPlotter:
    def __init__(self, dataset: DataSet, disp_freq_unit='nm'):
        """
        Class which can Plot a `DataSet` using matplotlib.

        Parameters
        ----------
        dataset : DataSet
            The DataSet to work with.
        disp_freq_unit : {'nm', 'cm'} (optional)
            The default unit of the plots. To change
            the unit afterwards, set the attribute directly.
        """
        self.dataset = dataset
        self.freq_unit = disp_freq_unit

    def map(self, symlog=True, equal_limits=True,
            plot_con=True, con_step=None, con_filter=None, ax=None,
            **kwargs):
        """
        Plot a colormap of the dataset with optional contour lines.

        Parameters
        ----------
        symlog : bool
            Determines if the yscale is symmetric logarithmic.
        equal_limits : bool
            If true, it makes to colors symmetric around zeros. Note this
            also sets the middle of the colormap to zero.
            Default is `True`.
        plot_con : bool
            Plot additional contour lines if `True` (default).
        con_step : float, array or None
            Controls the contour-levels. If `con_step` is a float, it is used as
            the step size between two levels. If it is an array, its elements
            are the levels. If `None`, it defaults to 20 levels.
        con_filter : None, int or `DataSet`.
            Since contours are strongly affected by noise, it can be prefered to
            filter the dataset before calculating the contours. If `con_filter`
            is a dataset, the data of that set will be used for the contours. If
            it is a tuple of int, the data will be filtered with an
            uniform filter before calculation the contours. If `None`, no data
            prepossessing will be applied.
        ax : plt.Axis or None
            Takes a matplotlib axis. If none, it uses `plt.gca()` to get the
            current axes. The lines are plotted in this axis.

        """
        if ax is None:
            ax = plt.gca()
        is_nm = self.freq_unit is 'nm'
        if is_nm:
            ph.vis_mode()
        else:
            ph.ir_mode()

        ds = self.dataset
        x = ds.wavelengths if is_nm else ds.wavenumbers
        cmap = kwargs.pop('colormap', "bwr")
        if equal_limits:
            m = np.max(np.abs(ds.data))
            vmin, vmax = -m, m
        else:
            vmin, vmax = ds.data.max(), ds.data.min()
        mesh = ax.pcolormesh(x, ds.t, ds.data, vmin=vmin,
                             vmax=vmax, cmap=cmap, **kwargs)
        if symlog:
            ax.set_yscale('symlog', linthreshy=1)
            ph.symticks(ax, axis='y')
            ax.set_ylim(-.5)
        plt.colorbar(mesh, ax=ax)

        if plot_con:
            if con_step is None:
                levels = 20
            elif isinstance(con_step, np.ndarray):
                levels = con_step
            else:
                # TODO This assumes data has positive and negative elements.
                pos = np.arange(0, ds.data.max(), con_step)
                neg = np.arange(0, -ds.data.min(), con_step)
                levels = np.hstack((-neg[::-1][:-1], pos))

            if isinstance(con_filter, DataSet):
                data = con_filter.data
            elif con_filter is not None:  # must be int or tuple of int
                if isinstance(con_filter, tuple):
                    data = uniform_filter(ds, con_filter).data
                else:
                    data = svd_filter(ds, con_filter).data
            else:
                data = ds.data
            ax.contour(x, ds.t, data, levels=levels,
                       linestyles='solid', colors='k', linewidths=0.5)
        ph.lbl_map(ax, symlog)
        if not is_nm:
            ax.set_xlim(*ax.get_xlim()[::-1])

    def spec(self, t_list, norm=False, ax=None, n_average=0, **kwargs):
        """
        Plot spectra at given times.

        Parameters
        ----------
        t_list : list or ndarray
            List of the times where the spectra are plotted.
        norm : bool
            If true, each spectral will be normalized.
        ax : plt.Axis or None.
            Axis where the spectra are plotted. If none, the current axis will
            be used.
        n_average : int
            For noisy data it may be prefered to average multiple spectra
            together. This function plots the average of `n_average` spectra
            around the specific time-points.

        Returns
        -------
        list of `Lines2D`
            List containing the Line2D objects belonging to the spectra.
        """

        if ax is None:
            ax = plt.gca()
        is_nm = self.freq_unit == 'nm'
        if is_nm:
            ph.vis_mode()
        else:
            ph.ir_mode()
        ds = self.dataset
        x = ds.wavelengths if is_nm else ds.wavenumbers
        li = []
        for i in t_list:
            idx = dv.fi(ds.t, i)
            if n_average > 0:
                dat = uniform_filter(ds, (2 * n_average + 1, 1)).data[idx, :]
            elif n_average == 0:
                dat = ds.data[idx, :]
            else:
                raise ValueError(
                    'n_average must be an Integer >= 0.')

            if norm:
                dat = dat / abs(dat).max()
            li += ax.plot(x, dat,
                          label=ph.time_formatter(ds.t[idx], ph.time_unit),
                          **kwargs)

        ax.set_xlabel(ph.freq_label)
        ax.set_ylabel(ph.sig_label)
        ax.autoscale(1, 'x', 1)
        ax.axhline(0, color='k', lw=0.5, zorder=1.9)
        ax.legend(loc='best', ncol=2, title='Delay time')
        ax.minorticks_on()
        return li

    def trans(self, wls, symlog=True, norm=False, ax=None,
              **kwargs):
        """
        Plot the nearest transients for given frequencies.

        Parameters
        ----------
        wls : list or ndarray
            Spectral positions, should be given in the same unit as
            `self.freq_unit`.
        symlog : bool
            Determines if the x-scale is symlog.
        norm : bool or float
            If `False`, no normalization is used. If `True`, each transient
            is divided by the maximum absolute value. If `norm` is a float,
            all transient are normalized by their signal at the time `norm`.
        ax : plt.Axes or None
            Takes a matplotlib axes. If none, it uses `plt.gca()` to get the
            current axes. The lines are plotted in this axis.

        All other kwargs are forwarded to the plot function.

        Returns
        -------
         list of Line2D
            List containing the plotted lines.
        """
        if ax is None:
            ax = plt.gca()
        is_nm = self.freq_unit == 'nm'
        if is_nm:
            ph.vis_mode()
        else:
            ph.ir_mode()
        ds = self.dataset
        x = ds.wavelengths if is_nm else ds.wavenumbers

        wl, t, d = ds.wl, ds.t, ds.data
        l, plotted_vals = [], []
        for i in wls:
            idx = dv.fi(x, i)

            dat = d[:, idx]
            if norm is True:
                dat = np.sign(dat[np.argmax(abs(dat))]) * dat / abs(dat).max()
            elif norm is False:
                pass
            else:
                dat = dat / dat[dv.fi(t, norm)]
            plotted_vals.append(dat)
            l.extend(ax.plot(t, dat, label='%.1f %s' % (x[idx], ph.freq_unit),
                             **kwargs))

        if symlog:
            ax.set_xscale('symlog', linthreshx=1.)
        ph.lbl_trans(ax=ax, use_symlog=symlog)
        ax.legend(loc='best', ncol=3)
        ax.set_xlim(right=t.max())
        ax.yaxis.set_tick_params(which='minor', left=True)
        return l

    def overview(self):
        """
        Plots an overview figure.
        """
        is_nm = self.freq_unit is 'nm'
        if is_nm:
            ph.vis_mode()
        else:
            ph.ir_mode()
        ds = self.dataset
        x = ds.wavelengths if is_nm else ds.wavenumbers
        fig, axs = plt.subplots(3, 1, figsize=(5, 12),
                                gridspec_kw=dict(height_ratios=(2, 1, 1)))
        self.map(ax=axs[0])

        times = np.hstack((0, np.geomspace(0.1, ds.t.max(), 6)))
        sp = self.spec(times, ax=axs[1])
        freqs = np.unique(np.linspace(x.min(), x.max(), 6))
        tr = self.trans(freqs, ax=axs[2])
        OverviewPlot = namedtuple('OverviewPlot', 'fig axs trans spec')
        return OverviewPlot(fig, axs, tr, sp)

    def svd(self, n=5):
        """
        Plot the SVD-components of the dataset.

        Parameters
        ----------
        n : int or list of int
            Determines the plotted SVD-components. If `n` is an int, it plots
            the first n components. If `n` is a list of ints, then every
            number is a SVD-component to be plotted.
        """
        is_nm = self.freq_unit is 'nm'
        if is_nm:
            ph.vis_mode()
        else:
            ph.ir_mode()
        ds = self.dataset
        x = ds.wavelengths if is_nm else ds.wavenumbers
        fig, axs = plt.subplots(3, 1, figsize=(4, 5))
        u, s, v = np.linalg.svd(ds.data)
        axs[0].stem(s)
        axs[0].set_xlim(0, 11)
        try:
            len(n)
            comps = n
        except TypeError:
            comps = range(n)

        for i in comps:
            axs[1].plot(ds.t, u.T[i], label='%d' % i)
            axs[2].plot(x, v[i])
        ph.lbl_trans(axs[1], use_symlog=True)
        ph.lbl_spec(axs[2])


class DataSetInteractiveViewer:
    def __init__(self, dataset, fig_kws=None):
        """
        Class showing a interactive matplotlib window for exploring
        a dataset.
        """
        if fig_kws is None:
            fig_kws = {}

        self.dataset = dataset
        self.figure, axs = plt.subplots(3, 1, **fig_kws)
        self.ax_img, self.ax_trans, self.ax_spec = axs
        self.ax_img.pcolormesh(dataset.wl, dataset.t, dataset.data)
        self.ax_img.set_yscale('symlog', linscaley=1)

        self.trans_line = self.ax_trans.plot()
        self.spec_line = self.ax_spec.plot()

    def init_event(self):
        """Connect mpl events"""
        connect = self.figure.canvas.mpl_connect
        connect('motion_notify_event', self.update_lines)

    def update_lines(self, event):
        """If the mouse cursor is over the 2D image, update
        the dynamic transient and spectrum"""
        pass
