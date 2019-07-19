#
# ICRAR - International Centre for Radio Astronomy Research
# (c) UWA - The University of Western Australia, 2019
# Copyright by UWA (in the framework of the ICRAR)
#
# Originally contributed by Mawson Sammons
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""
Constraints for optimizers to evaluate shark models against observations
"""

import logging
import os
import re

import analysis
import common
import numpy as np
import smf


logger = logging.getLogger(__name__)

GyrToYr = 1e9

#######################
# Binning configuration
mlow = 5
mupp = 14
dm = 0.2
mbins = np.arange(mlow, mupp, dm)
xmf = mbins + dm/2.0

mlow2 = 5
mupp2 = 14
dm2 = 0.3
mbins2 = np.arange(mlow2,mupp2,dm2)
xmf2 = mbins2 + dm2/2.0

ssfrlow = -6
ssfrupp = 4
dssfr = 0.2
ssfrbins = np.arange(ssfrlow,ssfrupp,dssfr)


# These are two easily create variables of these different shapes without
# actually storing a reference ourselves; we don't need it
zeros1 = lambda: np.zeros(shape=(1, 3, len(xmf)))
zeros2 = lambda: np.zeros(shape=(1, 3, len(xmf2)))
zeros3 = lambda: np.zeros(shape=(1, len(mbins)))
zeros4 = lambda: np.empty(shape=(1), dtype=np.bool_)
zeros5 = lambda: np.zeros(shape=(1, len(ssfrbins)))


class Constraint(object):
    """Base classes for constraint objects"""

    convert_to_multiple_batches = True

    def __init__(self):
        self.redshift_table = None
        self.weight = 1
        self.rel_weight = 1

    def _load_model_data(self, modeldir, subvols):

        if  len(subvols) > 1 and self.convert_to_multiple_batches:
            subvols = ["multiple_batches"]

        # Histograms we are interested in
        hist_smf = zeros3()
        hist_HImf = zeros3()

        fields = {
            'galaxies': (
                'sfr_disk', 'sfr_burst', 'mstars_disk', 'mstars_bulge',
                'rstar_disk', 'm_bh', 'matom_disk', 'mmol_disk', 'mgas_disk',
                'matom_bulge', 'mmol_bulge', 'mgas_bulge',
                'mgas_metals_disk', 'mgas_metals_bulge',
                'mstars_metals_disk', 'mstars_metals_bulge', 'type',
                'mvir_hosthalo', 'rstar_bulge')
        }

        for index, z in enumerate(self.z):
            hdf5_data = common.read_data(modeldir, self.redshift_table[z], fields, subvols)
            h0 = hdf5_data[0]
            smf.prepare_data(hdf5_data, index, hist_smf, zeros3(), zeros3(),
                             zeros3(), zeros3(), hist_HImf, zeros3(), zeros3(),
                             zeros3(), zeros3(), zeros3(), zeros1(), zeros1(),
                             zeros1(), zeros1(), zeros1(), zeros1(), zeros1(),
                             zeros1(), zeros1(), zeros1(), zeros1(), zeros1(),
                             zeros1(), zeros4(), zeros4(), zeros2(), zeros5(),
                             zeros1(), zeros1(), zeros1(), zeros1(), zeros1(),
                             zeros1())

        #########################
        # take logs
        ind = np.where(hist_smf > 0.)
        hist_smf[ind] = np.log10(hist_smf[ind])
        ind = np.where(hist_HImf > 0.)
        hist_HImf[ind] = np.log10(hist_HImf[ind])

        return h0, hist_smf, hist_HImf

    def load_observation(self, *args, **kwargs):
        obsdir = os.path.normpath(os.path.abspath(os.path.join(__file__, '..', '..', 'data')))
        return common.load_observation(obsdir, *args, **kwargs)

    def _get_raw_data(self, modeldir, subvols):
        """Gets the model and observational data for further analysis.
        The model data is interpolated to match the observation's X values."""

        h0, hist_smf, hist_HImf = self._load_model_data(modeldir, subvols)
        x_obs, y_obs, y_dn, y_up = self.get_obs_x_y_err(h0)
        x_mod, y_mod = self.get_model_x_y(hist_smf, hist_HImf)
        return x_obs, y_obs, y_dn, y_up, x_mod, y_mod

    def get_data(self, modeldir, subvols, plot_outputdir=None):

        x_obs, y_obs, y_dn, y_up, x_mod, y_mod = self._get_raw_data(modeldir, subvols)

        # Both observations and model values don't come necessarily in order,
        # but if at the end of the day we want to perform array-wise operations
        # over them (to calculate chi2 or student-t) then they should be both in
        # ascending order
        sorted_obs = np.argsort(x_obs)
        x_obs = x_obs[sorted_obs]
        y_obs = y_obs[sorted_obs]
        y_dn = y_dn[sorted_obs]
        y_up = y_up[sorted_obs]
        sorted_mod = np.argsort(x_mod)
        x_mod = x_mod[sorted_mod]
        y_mod = y_mod[sorted_mod]

        # Linearly interpolate model Y values respect to the observations'
        # X values, and only take those within the domain.
        # We also consider the biggest relative error as "the" error, in case
        # they are different
        y_mod_interp = np.interp(x_obs, x_mod, y_mod)
        sel = np.where((x_obs >= self.domain[0]) & (x_obs <= self.domain[1]))
        err = np.maximum(np.abs(y_dn[sel]), np.abs(y_up[sel]))
        x_obs_sel = x_obs[sel]
        y_obs_sel = y_obs[sel]
        y_mod_sel = y_mod_interp[sel]

        if plot_outputdir:
            self.plot(plot_outputdir,
                      x_obs, y_obs, y_dn, y_up,
                      x_mod, y_mod, y_mod_interp,
                      x_obs_sel, y_obs_sel, y_mod_sel, err)

        return y_obs_sel, y_mod_sel, err

    def plot(self, plot_outputdir, x_obs, y_obs, obs_err_dn, obs_err_up,
             x_mod, y_mod, y_mod_interp, x_obs_sel, y_obs_sel, y_mod_sel, err):
        fig = common.load_matplotlib().figure(figsize=(4.5,4.5))
        ax = fig.add_subplot(111)
        ax.axvline(self.domain[0], ls='dotted', c='red')
        ax.axvline(self.domain[1], ls='dotted', c='red')
        ax.plot(x_obs_sel, y_obs_sel, marker='v', ls='None', c='blue', label="Selected observations")
        ax.plot(x_mod, y_mod, marker='^', ls='solid', c='orange', label="Model")
        ax.plot(x_obs, y_mod_interp, ls='solid', c='green', label="Interpolated model")
        ax.plot(x_obs_sel, y_mod_sel, ls='solid', c='brown', label="Selected model")
        common.errorbars(ax, x_obs, y_obs, obs_err_dn, obs_err_up, 'black', '+',
                         err_absolute=False, label="Observations")

        common.prepare_legend(ax, ['blue', 'orange', 'green', 'brown', 'black'])

        chi2 = analysis.chi2(y_obs_sel, y_mod_sel, err)
        st = analysis.studentT(y_obs_sel, y_mod_sel, err)
        ax.set_title('%s\nchi2 = %g, student-t = %g' % (str(self), chi2, st))

        common.savefig(plot_outputdir, fig, str(self))

    def __str__(self):
        s = '%s(%.1f-%.1f)'
        args = self.__class__.__name__, self.domain[0], self.domain[1]
        if self.weight != 1:
            s += ', weight=%.2f, rel_weight=%.2f'
            args += self.weight, self.rel_weight
        return s % args


class HIMF(Constraint):
    """The HI Mass Function constraint"""

    domain = (7, 12)
    z = [0]

    def get_obs_x_y_err(self, h0):
        # Load Jones18 data and correct data for their choice of cosmology
        lmHI, pHI, pdnHI, pduHI = self.load_observation('mf/GasMF/HIMF_Jones18.dat', cols=[0,1,2,3])
        dpdnHI = pHI - pdnHI
        dpupHI = pduHI - pHI
        hobs = 0.7
        x_obs = lmHI + np.log10(pow(hobs, 2) / pow(h0, 2))
        y_obs = pHI + np.log10(pow(h0, 3) / pow(hobs, 3))
        y_dn = dpdnHI
        y_up = dpupHI
        return x_obs, y_obs, y_dn, y_up

    def get_model_x_y(self, _, hist_HImf):
        y = hist_HImf[0]
        ind = np.where(y < 0.)
        return xmf[ind], y[ind]

class SMF(Constraint):
    """Common logic for SMF constraints"""

    domain = (8, 13)

    def get_model_x_y(self, hist_smf, _):
        y = hist_smf[0,:]
        ind = np.where(y < 0.)
        return xmf[ind], y[ind]

class SMF_z0(SMF):
    """The SMF constraint at z=0"""

    z = [0]

    def get_obs_x_y_err(self, _):

        lm, p, dpdn, dpup = self.load_observation('mf/SMF/GAMAII_BBD_GSMFs.dat', cols=[0,1,2,3])
        indx = np.where(p > 0)
        x_obs = lm[indx]
        y_obs = np.log10(p[indx])
        ytemp = p[indx] - dpdn[indx]
        temp = np.less(ytemp, 0)

        # fixing a problem where there were undefined values due to log of
        # negative values; negative values were given a minimum of 0.0001
        fixed = 0.0001 * temp + ytemp * np.invert(temp)

        y_dn = y_obs - np.log10(fixed)
        y_up = np.log10(p[indx]+dpup[indx]) - y_obs

        return x_obs, y_obs, y_dn, y_up

class SMF_z1(SMF):
    """The SMF constraint at z=1"""

    z = [1]

    def get_obs_x_y_err(self, _):

        # Wright et al. (2018, several reshifts). Assumes Chabrier IMF.
        zD17, lmD17, pD17, dp_dn_D17, dp_up_D17 = self.load_observation('mf/SMF/Wright18_CombinedSMF.dat', cols=[0,1,2,3,4])
        hobs = 0.7
        pD17 = pD17 - 3.0 * np.log10(hobs)
        lmD17 = lmD17 - np.log10(hobs)
        in_redshift = np.where(zD17 == 1)
        x_obs = lmD17[in_redshift]
        y_obs = pD17[in_redshift]
        y_dn = dp_dn_D17[in_redshift]
        y_up = dp_up_D17[in_redshift]

        return x_obs, y_obs, y_dn, y_up


def _evaluate(constraint, stat_test, modeldir, subvols, plot_outputdir):
    try:
        y_obs, y_mod, err = constraint.get_data(modeldir, subvols,
                                                plot_outputdir=plot_outputdir)
        return stat_test(y_obs, y_mod, err) * constraint.weight
    except:
        logger.exception('Error while evaluating constraint, returning 1e20')
        return 1e20


def evaluate(constraints, stat_test, modeldir, subvols, plot_outputdir=None):
    """Returns the evaluation of all constraints, as a total number (default)
    or as individual numbers for each constraint"""
    return [_evaluate(c, stat_test, modeldir, subvols, plot_outputdir)
            for c in constraints]


def log_results(constraints, results):
    """Emits a log message showing the function evaluation for `constraints`"""

    sums = [sum(result) for result in results]
    min_idx = min(enumerate(sums), key=lambda enumerated_sum: enumerated_sum[1])[0]
    min_flags = [idx == min_idx for idx in range(len(sums))]

    n_cols = len(constraints) + 1
    msg = 'Particle evaluation results per-particle, per-constraint:\n'
    args = ()
    msg += ' ' * 3 + ' '.join(["%20.20s"] * n_cols) + ' Min\n'
    args += tuple(constraints)
    args += 'Total',
    msg += ' ' * 3 + ' '.join(["=" * 20] * n_cols) + ' ===\n'
    for particle_num, (result, min_flag) in enumerate(zip(results, min_flags)):
        msg += '%2d' + ' ' + ' '.join(['%20e'] * len(constraints)) + ' %20e %2s\n'
        args += particle_num,
        args += tuple(result)
        args += sum(result),
        args += '*' if min_flag else '',
    logger.info(msg, *args)


_constraint_re = re.compile((r'([0-9_a-zA-Z]+)' # name
                              '(?:\(([0-9\.]+)-([0-9\.]+)\))?' # domain boundaries
                              '(?:\*([0-9\.]+))?')) # weight
def parse(spec):
    """Parses a comma-separated string of constraint names into a list of
    Constraint objects. Specific domain values can be specified in `spec`"""

    _constraints = {
        'HIMF': HIMF,
        'SMF_z0': SMF_z0,
        'SMF_z1': SMF_z1,
    }

    def _parse(s):
        m = _constraint_re.match(s)
        if not m or m.group(1) not in _constraints:
            raise ValueError('Constraint does not specify a valid constraint: %s' % s)
        c = _constraints[m.group(1)]()
        if m.group(2):
            dn, up = float(m.group(2)), float(m.group(3))
            if dn < c.domain[0]:
                raise ValueError('Constraint low boundary is lower than lowest value possible (%f < %f)' % (dn, c.domain[0]))
            if up > c.domain[1]:
                raise ValueError('Constraint up boundary is higher than lowest value possible (%f > %f)' % (up, c.domain[1]))
            c.domain = (dn, up)
        if m.group(4):
            c.weight = float(m.group(4))
        return c

    constraints = [_parse(s) for s in spec.split(',')]
    total_weight = sum([c.weight for c in constraints])
    for c in constraints:
        c.rel_weight = c.weight / total_weight
    return constraints