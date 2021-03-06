"""
Simulator.py

Reference simulator for nengo models.
"""

from __future__ import print_function

from collections import Mapping
import logging

import numpy as np

from nengo.builder import Builder
from nengo.utils.compat import StringIO
from nengo.utils.graphs import toposort
from nengo.utils.simulator import operator_depencency_graph

logger = logging.getLogger(__name__)


class SignalDict(dict):
    """Map from Signal -> ndarray

    This dict subclass ensures that the ndarray values aren't overwritten,
    and instead data are written into them, which ensures that
    these arrays never get copied, which wastes time and space.

    Use ``init`` to set the ndarray initially.
    """

    def __getitem__(self, obj):
        """SignalDict overrides __getitem__ for two reasons.

        1. so that scalars are returned as 0-d ndarrays
        2. so that a SignalView lookup returns a views of its base
        """
        if obj in self:
            return dict.__getitem__(self, obj)
        elif obj.base in self:
            # look up views as a fallback
            # --work around numpy's special case behaviour for scalars
            base_array = self[obj.base]
            try:
                # for some installations, this works
                itemsize = int(obj.dtype.itemsize)
            except TypeError:
                # other installations, this...
                itemsize = int(obj.dtype().itemsize)
            byteoffset = itemsize * obj.offset
            bytestrides = [itemsize * s for s in obj.elemstrides]
            view = np.ndarray(shape=obj.shape,
                              dtype=obj.dtype,
                              buffer=base_array.data,
                              offset=byteoffset,
                              strides=bytestrides)
            return view
        else:
            raise KeyError("%s has not been initialized. Please call "
                           "SignalDict.init first." % (str(obj)))

    def __setitem__(self, key, val):
        """Ensures that ndarrays stay in the same place in memory.

        Unlike normal dicts, this means that you cannot add a new key
        to a SignalDict using __setitem__. This is by design, to avoid
        silent typos when debugging Simulator. Every key must instead
        be explicitly initialized with SignalDict.init.
        """
        self.__getitem__(key)[...] = val

    def __str__(self):
        """Pretty-print the signals and current values."""
        sio = StringIO()
        for k in self:
            sio.write("%s %s\n" % (repr(k), repr(self[k])))
        return sio.getvalue()

    def init(self, signal, ndarray):
        """Set up a permanent mapping from signal -> ndarray."""
        dict.__setitem__(self, signal, ndarray)


class ProbeDict(Mapping):
    """Map from Probe -> ndarray

    This is more like a view on the dict that the simulator manipulates.
    However, for speed reasons, the simulator uses Python lists,
    and we want to return NumPy arrays. Additionally, this mapping
    is readonly, which is more appropriate for its purpose.
    """

    def __init__(self, raw):
        self.raw = raw

    def __getitem__(self, key):
        rval = self.raw[key]
        if isinstance(rval, list):
            rval = np.asarray(rval)
            rval.flags.writeable = False
        return rval

    def __str__(self):
        return str(self.raw)

    def __repr__(self):
        return repr(self.raw)

    def __iter__(self):
        return iter(self.raw)

    def __len__(self):
        return len(self.raw)


class Simulator(object):
    """Reference simulator for models."""

    def __init__(self, model, dt=0.001, seed=None, builder=Builder()):
        # Call the builder to build the model
        self.model = builder(model, dt)
        self.dt = dt

        # Use model seed as simulator seed if the seed is not provided
        # Note: seed is not used right now, but one day...
        self.seed = self.model.seed if seed is None else seed

        # -- map from Signal.base -> ndarray
        self.signals = SignalDict(__time__=np.asarray(0.0, dtype=np.float64))
        for op in self.model.operators:
            op.init_signals(self.signals, self.dt)

        self.dg = operator_depencency_graph(self.model.operators)
        self._step_order = [node for node in toposort(self.dg)
                            if hasattr(node, 'make_step')]
        self._steps = [node.make_step(self.signals, self.dt)
                       for node in self._step_order]

        self.n_steps = 0

        # Add built states to the probe dictionary
        self._probe_outputs = self.model.params

        # Provide a nicer interface to probe outputs
        self.data = ProbeDict(self._probe_outputs)

    def step(self):
        """Advance the simulator by `self.dt` seconds.
        """
        old_err = np.seterr(invalid='raise', divide='ignore')
        try:
            for step_fn in self._steps:
                step_fn()
        finally:
            np.seterr(**old_err)

        # -- probes signals -> probe buffers
        for probe in self.model.probes:
            period = (1 if probe.sample_every is None
                      else int(probe.sample_every / self.dt))
            if self.n_steps % period == 0:
                tmp = self.signals[self.model.sig_in[probe]].copy()
                self._probe_outputs[probe].append(tmp)

        self.signals['__time__'] += self.dt
        self.n_steps += 1

    def run(self, time_in_seconds):
        """Simulate for the given length of time."""
        steps = int(np.round(float(time_in_seconds) / self.dt))
        logger.debug("Running %s for %f seconds, or %d steps",
                     self.model.label, time_in_seconds, steps)
        self.run_steps(steps)

    def run_steps(self, steps):
        """Simulate for the given number of `dt` steps."""
        for i in range(steps):
            if i % 1000 == 0:
                logger.debug("Step %d", i)
            self.step()

    def trange(self, dt=None):
        dt = self.dt if dt is None else dt
        last_t = self.signals['__time__'] - self.dt
        n_steps = self.n_steps if dt is None else int(
            self.n_steps / (dt / self.dt))
        return np.linspace(0, last_t, n_steps)
