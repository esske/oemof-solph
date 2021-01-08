# -*- coding: utf-8 -*-

"""This module is designed to hold custom components with their classes and
associated individual constraints (blocks) and groupings.

Therefore this module holds the class definition and the block directly located
by each other.

SPDX-FileCopyrightText: Uwe Krien <krien@uni-bremen.de>
SPDX-FileCopyrightText: Simon Hilpert
SPDX-FileCopyrightText: Cord Kaldemeyer
SPDX-FileCopyrightText: Patrik Schönfeldt
SPDX-FileCopyrightText: Johannes Röder
SPDX-FileCopyrightText: jakob-wo
SPDX-FileCopyrightText: gplssm

SPDX-License-Identifier: MIT

"""

import logging

from oemof.network import network as on
from pyomo.core.base.block import SimpleBlock
from pyomo.environ import Binary
from pyomo.environ import BuildAction
from pyomo.environ import Constraint
from pyomo.environ import Expression
from pyomo.environ import NonNegativeReals
from pyomo.environ import Set
from pyomo.environ import Var

from oemof.solph.network import Bus
from oemof.solph.network import Flow
from oemof.solph.network import Sink
from oemof.solph.plumbing import sequence
from oemof.solph.options import Investment


class ElectricalBus(Bus):
    r"""A electrical bus object. Every node has to be connected to Bus. This
    Bus is used in combination with ElectricalLine objects for linear optimal
    power flow (lopf) calculations.

    Parameters
    ----------
    slack: boolean
        If True Bus is slack bus for network
    v_max: numeric
        Maximum value of voltage angle at electrical bus
    v_min: numeric
        Mininum value of voltag angle at electrical bus

    Note: This component is experimental. Use it with care.

    Notes
    -----
    The following sets, variables, constraints and objective parts are created
     * :py:class:`~oemof.solph.blocks.Bus`
    The objects are also used inside:
     * :py:class:`~oemof.solph.custom.ElectricalLine`

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slack = kwargs.get('slack', False)
        self.v_max = kwargs.get('v_max', 1000)
        self.v_min = kwargs.get('v_min', -1000)


class ElectricalLine(Flow):
    r"""An ElectricalLine to be used in linear optimal power flow calculations.
    based on angle formulation. Check out the Notes below before using this
    component!

    Parameters
    ----------
    reactance : float or array of floats
        Reactance of the line to be modelled

    Note: This component is experimental. Use it with care.

    Notes
    -----
    * To use this object the connected buses need to be of the type
      :py:class:`~oemof.solph.custom.ElectricalBus`.
    * It does not work together with flows that have set the attr.`nonconvex`,
      i.e. unit commitment constraints are not possible
    * Input and output of this component are set equal, therefore just use
      either only the input or the output to parameterize.
    * Default attribute `min` of in/outflows is overwritten by -1 if not set
      differently by the user

    The following sets, variables, constraints and objective parts are created
     * :py:class:`~oemof.solph.custom.ElectricalLineBlock`

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reactance = sequence(kwargs.get('reactance', 0.00001))

        # set input / output flow values to -1 by default if not set by user
        if self.nonconvex is not None:
            raise ValueError(
                ("Attribute `nonconvex` must be None for " +
                 "component `ElectricalLine` from {} to {}!").format(
                    self.input, self.output))
        if self.min is None:
            self.min = -1
        # to be used in grouping for all bidi flows
        self.bidirectional = True

    def constraint_group(self):
        return ElectricalLineBlock


class ElectricalLineBlock(SimpleBlock):
    r"""Block for the linear relation of nodes with type
    class:`.ElectricalLine`

    Note: This component is experimental. Use it with care.

    **The following constraints are created:**

    Linear relation :attr:`om.ElectricalLine.electrical_flow[n,t]`
        .. math::
            flow(n, o, t) =  1 / reactance(n, t) \\cdot ()
            voltage_angle(i(n), t) - volatage_angle(o(n), t), \\
            \forall t \\in \\textrm{TIMESTEPS}, \\
            \forall n \\in \\textrm{ELECTRICAL\_LINES}.

    TODO: Add equate constraint of flows

    **The following variable are created:**

    TODO: Add voltage angle variable

    TODO: Add fix slack bus voltage angle to zero constraint / bound

    TODO: Add tests
    """

    CONSTRAINT_GROUP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _create(self, group=None):
        """ Creates the linear constraint for the class:`ElectricalLine`
        block.

        Parameters
        ----------
        group : list
            List of oemof.solph.ElectricalLine (eline) objects for which
            the linear relation of inputs and outputs is created
            e.g. group = [eline1, eline2, ...]. The components inside the
            list need to hold a attribute `reactance` of type Sequence
            containing the reactance of the line.
        """
        if group is None:
            return None

        m = self.parent_block()

        # create voltage angle variables
        self.ELECTRICAL_BUSES = Set(initialize=[n for n in m.es.nodes
                                                if isinstance(n, ElectricalBus)])

        def _voltage_angle_bounds(block, b, t):
            return b.v_min, b.v_max

        self.voltage_angle = Var(self.ELECTRICAL_BUSES, m.TIMESTEPS,
                                 bounds=_voltage_angle_bounds)

        if True not in [b.slack for b in self.ELECTRICAL_BUSES]:
            # TODO: Make this robust to select the same slack bus for
            # the same problems
            bus = [b for b in self.ELECTRICAL_BUSES][0]
            logging.info(
                "No slack bus set,setting bus {0} as slack bus".format(
                    bus.label))
            bus.slack = True

        def _voltage_angle_relation(block):
            for t in m.TIMESTEPS:
                for n in group:
                    if n.input.slack is True:
                        self.voltage_angle[n.output, t].value = 0
                        self.voltage_angle[n.output, t].fix()
                    try:
                        lhs = m.flow[n.input, n.output, t]
                        rhs = 1 / n.reactance[t] * (
                            self.voltage_angle[n.input, t] -
                            self.voltage_angle[n.output, t])
                    except ValueError:
                        raise ValueError("Error in constraint creation",
                                         "of node {}".format(n.label))
                    block.electrical_flow.add((n, t), (lhs == rhs))

        self.electrical_flow = Constraint(group, m.TIMESTEPS, noruleinit=True)

        self.electrical_flow_build = BuildAction(
            rule=_voltage_angle_relation)


class Link(on.Transformer):
    """A Link object with 1...2 inputs and 1...2 outputs.

    Parameters
    ----------
    conversion_factors : dict
        Dictionary containing conversion factors for conversion of each flow.
        Keys are the connected tuples (input, output) bus objects.
        The dictionary values can either be a scalar or an iterable with length
        of time horizon for simulation.

    Note: This component is experimental. Use it with care.

    Notes
    -----
    The sets, variables, constraints and objective parts are created
     * :py:class:`~oemof.solph.custom.LinkBlock`

    Examples
    --------

    >>> from oemof import solph
    >>> bel0 = solph.Bus(label="el0")
    >>> bel1 = solph.Bus(label="el1")

    >>> link = solph.custom.Link(
    ...    label="transshipment_link",
    ...    inputs={bel0: solph.Flow(), bel1: solph.Flow()},
    ...    outputs={bel0: solph.Flow(), bel1: solph.Flow()},
    ...    conversion_factors={(bel0, bel1): 0.92, (bel1, bel0): 0.99})
    >>> print(sorted([x[1][5] for x in link.conversion_factors.items()]))
    [0.92, 0.99]

    >>> type(link)
    <class 'oemof.solph.custom.Link'>

    >>> sorted([str(i) for i in link.inputs])
    ['el0', 'el1']

    >>> link.conversion_factors[(bel0, bel1)][3]
    0.92
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.conversion_factors = {
            k: sequence(v)
            for k, v in kwargs.get('conversion_factors', {}).items()}

        wrong_args_message = "Component `Link` must have exactly" \
                             + "2 inputs, 2 outputs, and 2" \
                             + "conversion factors connecting these."
        assert len(self.inputs) == 2, wrong_args_message
        assert len(self.outputs) == 2, wrong_args_message
        assert len(self.conversion_factors) == 2, wrong_args_message

    def constraint_group(self):
        return LinkBlock


class LinkBlock(SimpleBlock):
    r"""Block for the relation of nodes with type
    :class:`~oemof.solph.custom.Link`

    Note: This component is experimental. Use it with care.

    **The following constraints are created:**

    TODO: Add description for constraints
    TODO: Add tests

    """
    CONSTRAINT_GROUP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _create(self, group=None):
        """ Creates the relation for the class:`Link`.

        Parameters
        ----------
        group : list
            List of oemof.solph.custom.Link objects for which
            the relation of inputs and outputs is createdBuildAction
            e.g. group = [link1, link2, link3, ...]. The components inside
            the list need to hold an attribute `conversion_factors` of type
            dict containing the conversion factors for all inputs to outputs.
        """
        if group is None:
            return None

        m = self.parent_block()

        all_conversions = {}
        for n in group:
            all_conversions[n] = {
                k: v for k, v in n.conversion_factors.items()}

        def _input_output_relation(block):
            for t in m.TIMESTEPS:
                for n, conversion in all_conversions.items():
                    for cidx, c in conversion.items():
                        try:
                            expr = (m.flow[n, cidx[1], t] ==
                                    c[t] * m.flow[cidx[0], n, t])
                        except ValueError:
                            raise ValueError(
                                "Error in constraint creation",
                                "from: {0}, to: {1}, via: {2}".format(
                                    cidx[0], cidx[1], n))
                        block.relation.add((n, cidx[0], cidx[1], t), (expr))

        self.relation = Constraint(
            [(n, cidx[0], cidx[1], t)
             for t in m.TIMESTEPS
             for n, conversion in all_conversions.items()
             for cidx, c in conversion.items()], noruleinit=True)
        self.relation_build = BuildAction(rule=_input_output_relation)

        def _exclusive_direction_relation(block):
            for t in m.TIMESTEPS:
                for n, cf in all_conversions.items():
                    cf_keys = list(cf.keys())
                    expr = (m.flow[cf_keys[0][0], n, t] * cf[cf_keys[0]][t]
                            + m.flow[cf_keys[1][0], n, t] * cf[cf_keys[1]][t]
                            ==
                            m.flow[n, cf_keys[0][1], t]
                            + m.flow[n, cf_keys[1][1], t])
                    block.relation_exclusive_direction.add((n, t), expr)

        self.relation_exclusive_direction = Constraint(
            [(n, t)
             for t in m.TIMESTEPS
             for n, conversion in all_conversions.items()], noruleinit=True)
        self.relation_exclusive_direction_build = BuildAction(
            rule=_exclusive_direction_relation)


class GenericCAES(on.Transformer):
    """
    Component `GenericCAES` to model arbitrary compressed air energy storages.

    The full set of equations is described in:
    Kaldemeyer, C.; Boysen, C.; Tuschy, I.
    A Generic Formulation of Compressed Air Energy Storage as
    Mixed Integer Linear Program – Unit Commitment of Specific
    Technical Concepts in Arbitrary Market Environments
    Materials Today: Proceedings 00 (2018) 0000–0000
    [currently in review]

    Parameters
    ----------
    electrical_input : dict
        Dictionary with key-value-pair of `oemof.Bus` and `oemof.Flow` object
        for the electrical input.
    fuel_input : dict
        Dictionary with key-value-pair of `oemof.Bus` and `oemof.Flow` object
        for the fuel input.
    electrical_output : dict
        Dictionary with key-value-pair of `oemof.Bus` and `oemof.Flow` object
        for the electrical output.

    Note: This component is experimental. Use it with care.

    Notes
    -----
    The following sets, variables, constraints and objective parts are created
     * :py:class:`~oemof.solph.blocks.GenericCAES`

    TODO: Add description for constraints. See referenced paper until then!

    Examples
    --------

    >>> from oemof import solph
    >>> bel = solph.Bus(label='bel')
    >>> bth = solph.Bus(label='bth')
    >>> bgas = solph.Bus(label='bgas')
    >>> # dictionary with parameters for a specific CAES plant
    >>> concept = {
    ...    'cav_e_in_b': 0,
    ...    'cav_e_in_m': 0.6457267578,
    ...    'cav_e_out_b': 0,
    ...    'cav_e_out_m': 0.3739636077,
    ...    'cav_eta_temp': 1.0,
    ...    'cav_level_max': 211.11,
    ...    'cmp_p_max_b': 86.0918959849,
    ...    'cmp_p_max_m': 0.0679999932,
    ...    'cmp_p_min': 1,
    ...    'cmp_q_out_b': -19.3996965679,
    ...    'cmp_q_out_m': 1.1066036114,
    ...    'cmp_q_tes_share': 0,
    ...    'exp_p_max_b': 46.1294016678,
    ...    'exp_p_max_m': 0.2528340303,
    ...    'exp_p_min': 1,
    ...    'exp_q_in_b': -2.2073411014,
    ...    'exp_q_in_m': 1.129249765,
    ...    'exp_q_tes_share': 0,
    ...    'tes_eta_temp': 1.0,
    ...    'tes_level_max': 0.0}
    >>> # generic compressed air energy storage (caes) plant
    >>> caes = solph.custom.GenericCAES(
    ...    label='caes',
    ...    electrical_input={bel: solph.Flow()},
    ...    fuel_input={bgas: solph.Flow()},
    ...    electrical_output={bel: solph.Flow()},
    ...    params=concept, fixed_costs=0)
    >>> type(caes)
    <class 'oemof.solph.custom.GenericCAES'>
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.electrical_input = kwargs.get('electrical_input')
        self.fuel_input = kwargs.get('fuel_input')
        self.electrical_output = kwargs.get('electrical_output')
        self.params = kwargs.get('params')

        # map specific flows to standard API
        self.inputs.update(kwargs.get('electrical_input'))
        self.inputs.update(kwargs.get('fuel_input'))
        self.outputs.update(kwargs.get('electrical_output'))

    def constraint_group(self):
        return GenericCAESBlock


class GenericCAESBlock(SimpleBlock):
    r"""Block for nodes of class:`.GenericCAES`.

    Note: This component is experimental. Use it with care.

    **The following constraints are created:**

    .. _GenericCAES-equations:

    .. math::
        &
        (1) \qquad P_{cmp}(t) = electrical\_input (t)
            \quad \forall t \in T \\
        &
        (2) \qquad P_{cmp\_max}(t) = m_{cmp\_max} \cdot CAS_{fil}(t-1)
            + b_{cmp\_max}
            \quad \forall t \in\left[1, t_{max}\right] \\
        &
        (3) \qquad P_{cmp\_max}(t) = b_{cmp\_max}
            \quad \forall t \notin\left[1, t_{max}\right] \\
        &
        (4) \qquad P_{cmp}(t) \leq P_{cmp\_max}(t)
            \quad \forall t \in T  \\
        &
        (5) \qquad P_{cmp}(t) \geq P_{cmp\_min} \cdot ST_{cmp}(t)
            \quad \forall t \in T  \\
        &
        (6) \qquad P_{cmp}(t) = m_{cmp\_max} \cdot CAS_{fil\_max}
            + b_{cmp\_max} \cdot ST_{cmp}(t)
            \quad \forall t \in T \\
        &
        (7) \qquad \dot{Q}_{cmp}(t) =
            m_{cmp\_q} \cdot P_{cmp}(t) + b_{cmp\_q} \cdot ST_{cmp}(t)
            \quad \forall t \in T  \\
        &
        (8) \qquad \dot{Q}_{cmp}(t) = \dot{Q}_{cmp_out}(t)
            + \dot{Q}_{tes\_in}(t)
            \quad \forall t \in T \\
        &
        (9) \qquad r_{cmp\_tes} \cdot\dot{Q}_{cmp\_out}(t) =
            \left(1-r_{cmp\_tes}\right) \dot{Q}_{tes\_in}(t)
            \quad \forall t \in T \\
        &
        (10) \quad\; P_{exp}(t) = electrical\_output (t)
             \quad \forall t \in T \\
        &
        (11) \quad\; P_{exp\_max}(t) = m_{exp\_max} CAS_{fil}(t-1)
             + b_{exp\_max}
             \quad \forall t \in\left[1, t_{\max }\right] \\
        &
        (12) \quad\; P_{exp\_max}(t) = b_{exp\_max}
             \quad \forall t \notin\left[1, t_{\max }\right] \\
        &
        (13) \quad\; P_{exp}(t) \leq P_{exp\_max}(t)
             \quad \forall t \in T \\
        &
        (14) \quad\; P_{exp}(t) \geq P_{exp\_min}(t) \cdot ST_{exp}(t)
             \quad \forall t \in T \\
        &
        (15) \quad\; P_{exp}(t) \leq m_{exp\_max} \cdot CAS_{fil\_max}
             + b_{exp\_max} \cdot ST_{exp}(t)
             \quad \forall t \in T \\
        &
        (16) \quad\; \dot{Q}_{exp}(t) = m_{exp\_q} \cdot P_{exp}(t)
             + b_{cxp\_q} \cdot ST_{cxp}(t)
             \quad \forall t \in T \\
        &
        (17) \quad\; \dot{Q}_{exp\_in}(t) = fuel\_input(t)
             \quad \forall t \in T \\
        &
        (18) \quad\; \dot{Q}_{exp}(t) = \dot{Q}_{exp\_in}(t)
             + \dot{Q}_{tes\_out}(t)+\dot{Q}_{cxp\_add}(t)
             \quad \forall t \in T \\
        &
        (19) \quad\; r_{exp\_tes} \cdot \dot{Q}_{exp\_in}(t) =
             (1 - r_{exp\_tes})(\dot{Q}_{tes\_out}(t) + \dot{Q}_{exp\_add}(t))
             \quad \forall t \in T \\
        &
        (20) \quad\; \dot{E}_{cas\_in}(t) = m_{cas\_in}\cdot P_{cmp}(t)
             + b_{cas\_in}\cdot ST_{cmp}(t)
             \quad \forall t \in T \\
        &
        (21) \quad\; \dot{E}_{cas\_out}(t) = m_{cas\_out}\cdot P_{cmp}(t)
             + b_{cas\_out}\cdot ST_{cmp}(t)
             \quad \forall t \in T \\
        &
        (22) \quad\; \eta_{cas\_tmp} \cdot CAS_{fil}(t) = CAS_{fil}(t-1)
             + \tau\left(\dot{E}_{cas\_in}(t) - \dot{E}_{cas\_out}(t)\right)
             \quad \forall t \in\left[1, t_{max}\right] \\
         &
        (23) \quad\; \eta_{cas\_tmp} \cdot CAS_{fil}(t) =
             \tau\left(\dot{E}_{cas\_in}(t) - \dot{E}_{cas\_out}(t)\right)
             \quad \forall t \notin\left[1, t_{max}\right] \\
        &
        (24) \quad\; CAS_{fil}(t) \leq CAS_{fil\_max}
             \quad \forall t \in T \\
        &
        (25) \quad\; TES_{fil}(t) = TES_{fil}(t-1)
             + \tau\left(\dot{Q}_{tes\_in}(t)
             - \dot{Q}_{tes\_out}(t)\right)
             \quad \forall t \in\left[1, t_{max}\right] \\
         &
        (26) \quad\; TES_{fil}(t) =
             \tau\left(\dot{Q}_{tes\_in}(t)
             - \dot{Q}_{tes\_out}(t)\right)
             \quad \forall t \notin\left[1, t_{max}\right] \\
        &
        (27) \quad\; TES_{fil}(t) \leq TES_{fil\_max}
             \quad \forall t \in T \\
        &


    **Table: Symbols and attribute names of variables and parameters**

    .. csv-table:: Variables (V) and Parameters (P)
        :header: "symbol", "attribute", "type", "explanation"
        :widths: 1, 1, 1, 1

        ":math:`ST_{cmp}` ", ":py:obj:`cmp_st[n,t]` ", "V", "Status of
        compression"
        ":math:`{P}_{cmp}` ", ":py:obj:`cmp_p[n,t]`", "V", "Compression power"
        ":math:`{P}_{cmp\_max}`", ":py:obj:`cmp_p_max[n,t]`", "V", "Max.
        compression power"
        ":math:`\dot{Q}_{cmp}` ", ":py:obj:`cmp_q_out_sum[n,t]`", "V", "Summed
         heat flow in compression"
        ":math:`\dot{Q}_{cmp\_out}` ", ":py:obj:`cmp_q_waste[n,t]`", "V", "
        Waste heat flow from compression"
        ":math:`ST_{exp}(t)`", ":py:obj:`exp_st[n,t]`", "V", "Status of
        expansion (binary)"
        ":math:`P_{exp}(t)`", ":py:obj:`exp_p[n,t]`", "V", "Expansion power"
        ":math:`P_{exp\_max}(t)`", ":py:obj:`exp_p_max[n,t]`", "V", "Max.
        expansion power"
        ":math:`\dot{Q}_{exp}(t)`", ":py:obj:`exp_q_in_sum[n,t]`", "V", "
        Summed heat flow in expansion"
        ":math:`\dot{Q}_{exp\_in}(t)`", ":py:obj:`exp_q_fuel_in[n,t]`", "V", "
        Heat (external) flow into expansion"
        ":math:`\dot{Q}_{exp\_add}(t)`", ":py:obj:`exp_q_add_in[n,t]`", "V", "
        Additional heat flow into expansion"
        ":math:`CAV_{fil}(t)`", ":py:obj:`cav_level[n,t]`", "V", "Filling level
         if CAE"
        ":math:`\dot{E}_{cas\_in}(t)`", ":py:obj:`cav_e_in[n,t]`", "V", "
        Exergy flow into CAS"
        ":math:`\dot{E}_{cas\_out}(t)`", ":py:obj:`cav_e_out[n,t]`", "V", "
        Exergy flow from CAS"
        ":math:`TES_{fil}(t)`", ":py:obj:`tes_level[n,t]`", "V", "Filling
        level of Thermal Energy Storage (TES)"
        ":math:`\dot{Q}_{tes\_in}(t)`", ":py:obj:`tes_e_in[n,t]`", "V", "Heat
         flow into TES"
        ":math:`\dot{Q}_{tes\_out}(t)`", ":py:obj:`tes_e_out[n,t]`", "V", "Heat
         flow from TES"
        ":math:`b_{cmp\_max}`", ":py:obj:`cmp_p_max_b[n,t]`", "P", "Specific
         y-intersection"
        ":math:`b_{cmp\_q}`", ":py:obj:`cmp_q_out_b[n,t]`", "P", "Specific
        y-intersection"
        ":math:`b_{exp\_max}`", ":py:obj:`exp_p_max_b[n,t]`", "P", "Specific
        y-intersection"
        ":math:`b_{exp\_q}`", ":py:obj:`exp_q_in_b[n,t]`", "P", "Specific
        y-intersection"
        ":math:`b_{cas\_in}`", ":py:obj:`cav_e_in_b[n,t]`", "P", "Specific
        y-intersection"
        ":math:`b_{cas\_out}`", ":py:obj:`cav_e_out_b[n,t]`", "P", "Specific
        y-intersection"
        ":math:`m_{cmp\_max}`", ":py:obj:`cmp_p_max_m[n,t]`", "P", "Specific
         slope"
        ":math:`m_{cmp\_q}`", ":py:obj:`cmp_q_out_m[n,t]`", "P", "Specific
         slope"
        ":math:`m_{exp\_max}`", ":py:obj:`exp_p_max_m[n,t]`", "P", "Specific
         slope"
        ":math:`m_{exp\_q}`", ":py:obj:`exp_q_in_m[n,t]`", "P", "Specific
         slope"
        ":math:`m_{cas\_in}`", ":py:obj:`cav_e_in_m[n,t]`", "P", "Specific
         slope"
        ":math:`m_{cas\_out}`", ":py:obj:`cav_e_out_m[n,t]`", "P", "Specific
         slope"
        ":math:`P_{cmp\_min}`", ":py:obj:`cmp_p_min[n,t]`", "P", "Min.
        compression power"
        ":math:`r_{cmp\_tes}`", ":py:obj:`cmp_q_tes_share[n,t]`", "P", "Ratio
         between waste heat flow and heat flow into TES"
        ":math:`r_{exp\_tes}`", ":py:obj:`exp_q_tes_share[n,t]`", "P", "Ratio
         between external heat flow into expansion and heat flows from TES and
          additional source"
        ":math:`\tau`", ":py:obj:`m.timeincrement[n,t]`", "P", "Time interval
         length"
        ":math:`TES_{fil\_max}`", ":py:obj:`tes_level_max[n,t]`", "P", "Max.
        filling level of TES"
        ":math:`CAS_{fil\_max}`", ":py:obj:`cav_level_max[n,t]`", "P", "Max.
         filling level of TES"
        ":math:`\tau`", ":py:obj:`cav_eta_tmp[n,t]`", "P", "Temporal efficiency
         (loss factor to take intertemporal losses into account)"
        ":math:`electrical\_input`", "
        :py:obj:`flow[list(n.electrical_input.keys())[0], n, t]`", "P", "
        Electr. power input into compression"
        ":math:`electrical\_output`", "
        :py:obj:`flow[n, list(n.electrical_output.keys())[0], t]`", "P", "
        Electr. power output of expansion"
        ":math:`fuel\_input`", "
        :py:obj:`flow[list(n.fuel_input.keys())[0], n, t]`", "P", "Heat input
         (external) into Expansion"

    """

    CONSTRAINT_GROUP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _create(self, group=None):
        """
        Create constraints for GenericCAESBlock.

        Parameters
        ----------
        group : list
            List containing `.GenericCAES` objects.
            e.g. groups=[gcaes1, gcaes2,..]
        """
        m = self.parent_block()

        if group is None:
            return None

        self.GENERICCAES = Set(initialize=[n for n in group])

        # Compression: Binary variable for operation status
        self.cmp_st = Var(self.GENERICCAES, m.TIMESTEPS, within=Binary)

        # Compression: Realized capacity
        self.cmp_p = Var(self.GENERICCAES, m.TIMESTEPS,
                         within=NonNegativeReals)

        # Compression: Max. Capacity
        self.cmp_p_max = Var(self.GENERICCAES, m.TIMESTEPS,
                             within=NonNegativeReals)

        # Compression: Heat flow
        self.cmp_q_out_sum = Var(self.GENERICCAES, m.TIMESTEPS,
                                 within=NonNegativeReals)

        # Compression: Waste heat
        self.cmp_q_waste = Var(self.GENERICCAES, m.TIMESTEPS,
                               within=NonNegativeReals)

        # Expansion: Binary variable for operation status
        self.exp_st = Var(self.GENERICCAES, m.TIMESTEPS, within=Binary)

        # Expansion: Realized capacity
        self.exp_p = Var(self.GENERICCAES, m.TIMESTEPS,
                         within=NonNegativeReals)

        # Expansion: Max. Capacity
        self.exp_p_max = Var(self.GENERICCAES, m.TIMESTEPS,
                             within=NonNegativeReals)

        # Expansion: Heat flow of natural gas co-firing
        self.exp_q_in_sum = Var(self.GENERICCAES, m.TIMESTEPS,
                                within=NonNegativeReals)

        # Expansion: Heat flow of natural gas co-firing
        self.exp_q_fuel_in = Var(self.GENERICCAES, m.TIMESTEPS,
                                 within=NonNegativeReals)

        # Expansion: Heat flow of additional firing
        self.exp_q_add_in = Var(self.GENERICCAES, m.TIMESTEPS,
                                within=NonNegativeReals)

        # Cavern: Filling levelh
        self.cav_level = Var(self.GENERICCAES, m.TIMESTEPS,
                             within=NonNegativeReals)

        # Cavern: Energy inflow
        self.cav_e_in = Var(self.GENERICCAES, m.TIMESTEPS,
                            within=NonNegativeReals)

        # Cavern: Energy outflow
        self.cav_e_out = Var(self.GENERICCAES, m.TIMESTEPS,
                             within=NonNegativeReals)

        # TES: Filling levelh
        self.tes_level = Var(self.GENERICCAES, m.TIMESTEPS,
                             within=NonNegativeReals)

        # TES: Energy inflow
        self.tes_e_in = Var(self.GENERICCAES, m.TIMESTEPS,
                            within=NonNegativeReals)

        # TES: Energy outflow
        self.tes_e_out = Var(self.GENERICCAES, m.TIMESTEPS,
                             within=NonNegativeReals)

        # Spot market: Positive capacity
        self.exp_p_spot = Var(self.GENERICCAES, m.TIMESTEPS,
                              within=NonNegativeReals)

        # Spot market: Negative capacity
        self.cmp_p_spot = Var(self.GENERICCAES, m.TIMESTEPS,
                              within=NonNegativeReals)

        # Compression: Capacity on markets
        def cmp_p_constr_rule(block, n, t):
            expr = 0
            expr += -self.cmp_p[n, t]
            expr += m.flow[list(n.electrical_input.keys())[0], n, t]
            return expr == 0

        self.cmp_p_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_p_constr_rule)

        # Compression: Max. capacity depending on cavern filling level
        def cmp_p_max_constr_rule(block, n, t):
            if t != 0:
                return (self.cmp_p_max[n, t] ==
                        n.params['cmp_p_max_m'] * self.cav_level[n, t - 1] +
                        n.params['cmp_p_max_b'])
            else:
                return self.cmp_p_max[n, t] == n.params['cmp_p_max_b']

        self.cmp_p_max_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_p_max_constr_rule)

        def cmp_p_max_area_constr_rule(block, n, t):
            return self.cmp_p[n, t] <= self.cmp_p_max[n, t]

        self.cmp_p_max_area_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_p_max_area_constr_rule)

        # Compression: Status of operation (on/off)
        def cmp_st_p_min_constr_rule(block, n, t):
            return (
                self.cmp_p[n, t] >= n.params['cmp_p_min'] * self.cmp_st[n, t])

        self.cmp_st_p_min_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_st_p_min_constr_rule)

        def cmp_st_p_max_constr_rule(block, n, t):
            return (self.cmp_p[n, t] <=
                    (n.params['cmp_p_max_m'] * n.params['cav_level_max'] +
                     n.params['cmp_p_max_b']) * self.cmp_st[n, t])

        self.cmp_st_p_max_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_st_p_max_constr_rule)

        # (7) Compression: Heat flow out
        def cmp_q_out_constr_rule(block, n, t):
            return (self.cmp_q_out_sum[n, t] ==
                    n.params['cmp_q_out_m'] * self.cmp_p[n, t] +
                    n.params['cmp_q_out_b'] * self.cmp_st[n, t])

        self.cmp_q_out_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_q_out_constr_rule)

        #  (8) Compression: Definition of single heat flows
        def cmp_q_out_sum_constr_rule(block, n, t):
            return (self.cmp_q_out_sum[n, t] == self.cmp_q_waste[n, t] +
                    self.tes_e_in[n, t])

        self.cmp_q_out_sum_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_q_out_sum_constr_rule)

        # (9) Compression: Heat flow out ratio
        def cmp_q_out_shr_constr_rule(block, n, t):
            return (self.cmp_q_waste[n, t] * n.params['cmp_q_tes_share'] ==
                    self.tes_e_in[n, t] * (1 - n.params['cmp_q_tes_share']))

        self.cmp_q_out_shr_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cmp_q_out_shr_constr_rule)

        # (10) Expansion: Capacity on markets
        def exp_p_constr_rule(block, n, t):
            expr = 0
            expr += -self.exp_p[n, t]
            expr += m.flow[n, list(n.electrical_output.keys())[0], t]
            return expr == 0

        self.exp_p_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_p_constr_rule)

        # (11-12) Expansion: Max. capacity depending on cavern filling level
        def exp_p_max_constr_rule(block, n, t):
            if t != 0:
                return (self.exp_p_max[n, t] ==
                        n.params['exp_p_max_m'] * self.cav_level[n, t - 1] +
                        n.params['exp_p_max_b'])
            else:
                return self.exp_p_max[n, t] == n.params['exp_p_max_b']

        self.exp_p_max_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_p_max_constr_rule)

        # (13)
        def exp_p_max_area_constr_rule(block, n, t):
            return self.exp_p[n, t] <= self.exp_p_max[n, t]

        self.exp_p_max_area_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_p_max_area_constr_rule)

        # (14) Expansion: Status of operation (on/off)
        def exp_st_p_min_constr_rule(block, n, t):
            return (
                self.exp_p[n, t] >= n.params['exp_p_min'] * self.exp_st[n, t])

        self.exp_st_p_min_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_st_p_min_constr_rule)

        # (15)
        def exp_st_p_max_constr_rule(block, n, t):
            return (self.exp_p[n, t] <=
                    (n.params['exp_p_max_m'] * n.params['cav_level_max'] +
                     n.params['exp_p_max_b']) * self.exp_st[n, t])

        self.exp_st_p_max_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_st_p_max_constr_rule)

        # (16) Expansion: Heat flow in
        def exp_q_in_constr_rule(block, n, t):
            return (self.exp_q_in_sum[n, t] ==
                    n.params['exp_q_in_m'] * self.exp_p[n, t] +
                    n.params['exp_q_in_b'] * self.exp_st[n, t])

        self.exp_q_in_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_q_in_constr_rule)

        # (17) Expansion: Fuel allocation
        def exp_q_fuel_constr_rule(block, n, t):
            expr = 0
            expr += -self.exp_q_fuel_in[n, t]
            expr += m.flow[list(n.fuel_input.keys())[0], n, t]
            return expr == 0

        self.exp_q_fuel_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_q_fuel_constr_rule)

        # (18) Expansion: Definition of single heat flows
        def exp_q_in_sum_constr_rule(block, n, t):
            return (self.exp_q_in_sum[n, t] == self.exp_q_fuel_in[n, t] +
                    self.tes_e_out[n, t] + self.exp_q_add_in[n, t])

        self.exp_q_in_sum_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_q_in_sum_constr_rule)

        # (19) Expansion: Heat flow in ratio
        def exp_q_in_shr_constr_rule(block, n, t):
            return (n.params['exp_q_tes_share'] * self.exp_q_fuel_in[n, t] ==
                    (1 - n.params['exp_q_tes_share']) *
                    (self.exp_q_add_in[n, t] + self.tes_e_out[n, t]))

        self.exp_q_in_shr_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=exp_q_in_shr_constr_rule)

        # (20) Cavern: Energy inflow
        def cav_e_in_constr_rule(block, n, t):
            return (self.cav_e_in[n, t] ==
                    n.params['cav_e_in_m'] * self.cmp_p[n, t] +
                    n.params['cav_e_in_b'])

        self.cav_e_in_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cav_e_in_constr_rule)

        # (21) Cavern: Energy outflow
        def cav_e_out_constr_rule(block, n, t):
            return (self.cav_e_out[n, t] ==
                    n.params['cav_e_out_m'] * self.exp_p[n, t] +
                    n.params['cav_e_out_b'])

        self.cav_e_out_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cav_e_out_constr_rule)

        # (22-23) Cavern: Storage balance
        def cav_eta_constr_rule(block, n, t):
            if t != 0:
                return (n.params['cav_eta_temp'] * self.cav_level[n, t] ==
                        self.cav_level[n, t - 1] + m.timeincrement[t] *
                        (self.cav_e_in[n, t] - self.cav_e_out[n, t]))
            else:
                return (n.params['cav_eta_temp'] * self.cav_level[n, t] ==
                        m.timeincrement[t] *
                        (self.cav_e_in[n, t] - self.cav_e_out[n, t]))

        self.cav_eta_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cav_eta_constr_rule)

        # (24) Cavern: Upper bound
        def cav_ub_constr_rule(block, n, t):
            return self.cav_level[n, t] <= n.params['cav_level_max']

        self.cav_ub_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=cav_ub_constr_rule)

        # (25-26) TES: Storage balance
        def tes_eta_constr_rule(block, n, t):
            if t != 0:
                return (self.tes_level[n, t] ==
                        self.tes_level[n, t - 1] + m.timeincrement[t] *
                        (self.tes_e_in[n, t] - self.tes_e_out[n, t]))
            else:
                return (self.tes_level[n, t] ==
                        m.timeincrement[t] *
                        (self.tes_e_in[n, t] - self.tes_e_out[n, t]))

        self.tes_eta_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=tes_eta_constr_rule)

        # (27) TES: Upper bound
        def tes_ub_constr_rule(block, n, t):
            return self.tes_level[n, t] <= n.params['tes_level_max']

        self.tes_ub_constr = Constraint(
            self.GENERICCAES, m.TIMESTEPS, rule=tes_ub_constr_rule)


class SinkDSM(Sink):
    r"""
    Demand Side Management implemented as Sink with flexibility potential.

    There are several approaches possible which can be selected:
    - DIW: Based on the paper by Zerrahn, Alexander and Schill, Wolf-Peter (2015):
    `On the representation of demand-side management in power system models
    <https://doi.org/10.1016/j.energy.2015.03.037>`_,
    in: Energy (84), pp. 840-845, 10.1016/j.energy.2015.03.037,
    accessed 08.01.2021, pp. 842-843.
    - DLR: Based on the PhD thesis of Gils, Hans Christian (2015):
    `Balancing of Intermittent Renewable Power Generation by Demand Response and
    Thermal Energy Storage`, Stuttgart, <http://dx.doi.org/10.18419/opus-6888>,
    accessed 08.01.2021, pp. 67-70.
    - oemof: Created by Julian Endres. A fairly simple DSM representation which
    demands the energy balance to be levelled out in fixed cycles

    SinkDSM adds additional constraints that allow to shift energy in certain
    time window constrained by :attr:`~capacity_up` and
    :attr:`~capacity_down`.

    Parameters
    ----------
    demand: numeric
        original electrical demand (normalized)
    capacity_up: int or array
        maximum DSM capacity that may be increased (normalized)
    capacity_down: int or array
        maximum DSM capacity that may be reduced (normalized)
    approach: 'oemof', 'DIW', 'DLR'
        Choose one of the DSM modeling approaches. Read notes about which
        parameters to be applied for which approach.

        oemof :

            Simple model in which the load shift must be compensated in a
            predefined fixed interval (:attr:`~shift_interval` is mandatory).
            Within time windows of the length :attr:`~shift_interval` DSM
            up and down shifts are balanced. See
            :class:`~SinkDSMOemofBlock` for details.

        DIW :

            Sophisticated model based on the formulation by
            Zerrahn & Schill (2015a). The load-shift of the component must be
            compensated in a predefined delay-time (:attr:`~delay_time` is
            mandatory).
            For details see :class:`~SinkDSMDIWBlock`.

        DLR :

            Sophisticated model based on the formulation by
            Gils (2015). The load-shift of the component must be
            compensated in a predefined delay-time (:attr:`~delay_time` is
            mandatory).
            For details see :class:`~SinkDSMDLRBlock`.
    shift_interval: int
        Only used when :attr:`~approach` is set to 'oemof'. Otherwise, can be
        None.
        It's the interval in which between :math:`DSM_{t}^{up}` and
        :math:`DSM_{t}^{down}` have to be compensated.
    delay_time: int
        Only used when :attr:`~approach` is set to 'DIW' or 'DLR'. Otherwise, can be
        None.
        Length of symmetrical time windows around :math:`t` in which
        :math:`DSM_{t}^{up}` and :math:`DSM_{t,tt}^{down}` have to be
        compensated.
    shed_time: int
        Maximum length of a load shedding process at full capacity
        (used within energy limit constraint)
    max_demand: numeric
        the maximum demand prior to demand response
    max_capacity_down: numeric
        the maximum capacity eligible for downshifts
        prior to demand response (used for dispatch mode)
    max_capacity_up: numeric
        the maximum capacity eligible for upshifts
        prior to demand response (used for dispatch mode)
    flex_share_down: float
        flexible share of installed capacity
        eligible for downshifts (used for invest mode)
    flex_share_up: float
        flexible share of installed capacity
        eligible for upshifts (used for invest mode)
    cost_dsm_up : :obj:`int`
        Cost per unit of DSM activity that increases the demand
    cost_dsm_down_shift : :obj:`int`
        Cost per unit of DSM activity that decreases the demand
        for load shifting
    cost_dsm_down_shed : :obj:`int`
        Cost per unit of DSM activity that decreases the demand
        for load shedding
    efficiency : :obj:`float`
        Efficiency factor for load shifts (between 0 and 1)
    recovery_time_shift : :obj:`int`
        Minimum time between the end of one load shifting process
        and the start of another for load shifting processes
    recovery_time_shed : :obj:`int`
        Minimum time between the end of one load shifting process
        and the start of another for load shedding processes
    shed_eligibility : :obj:`boolean`
        Boolean parameter indicating whether unit is eligible for
        load shedding
    shift_eligibility : :obj:`boolean`
        Boolean parameter indicating whether unit is eligible for
        load shifting

    Note
    ----

    * This component is a candidate component. It's implemented as a custom
      component for users that like to use and test the component at early
      stage. Please report issues to improve the component.
    * As many constraints and dependencies are created in approach 'DIW',
      computational cost might be high with a large 'delay_time' and with model
      of high temporal resolution
    * Using :attr:`~approach` 'delay' might result in demand shifts that exceed
      the specified delay time by activating up and down simultaneously in
      the time steps between to DSM events.
    * It's not recommended to assign cost to the flow that connects
      :class:`~SinkDSM` with a bus. Instead, use :attr:`~SinkDSM.cost_dsm_up`
      or :attr:`~cost_dsm_down_shift`

    """

    def __init__(self, demand, capacity_up, capacity_down, approach,
                 shift_interval=None, delay_time=None, shed_time=None,
                 max_demand=None, max_capacity_down=None,
                 max_capacity_up=None,
                 flex_share_down=None, flex_share_up=None,
                 cost_dsm_up=0, cost_dsm_down_shift=0, cost_dsm_down_shed=0,
                 efficiency=1, recovery_time_shift=None,
                 recovery_time_shed=None, shed_eligibility=True,
                 shift_eligibility=True, **kwargs):
        super().__init__(**kwargs)

        self.capacity_up = sequence(capacity_up)
        self.capacity_down = sequence(capacity_down)
        self.demand = sequence(demand)
        self.approach = approach
        self.shift_interval = shift_interval
        self.delay_time = delay_time
        self.shed_time = shed_time

        # Attributes are only needed if no investments occur
        self.max_capacity_down = max_capacity_down
        self.max_capacity_up = max_capacity_up
        self.max_demand = max_demand

        # calculate flex shares:
        # max_capacity_down is a certain share of installed capacity that
        # will be kept constant for investment modeling and be used to
        # determine investments in shifting capacity
        if flex_share_down is not None:
            if max_capacity_down is None and max_demand is None:
                self.flex_share_down = flex_share_down
            else:
                e1 = ("Please determine either flex_share_down "
                      "(investment modeling)\n or set"
                      "max_demand and max_capacity_down (dispatch modeling).\n"
                      "Otherwise, overdetermination occurs.")
                raise AttributeError(e1)
        else:
            self.flex_share_down = self.max_capacity_down / self.max_demand

        if flex_share_up is not None:
            if max_capacity_up is None and max_demand is None:
                self.flex_share_up = flex_share_up
            else:
                e2 = ("Please determine either flex_share_up "
                      "(investment modeling)\n or set"
                      "max_demand and max_capacity_up (dispatch modeling).\n"
                      "Otherwise, overdetermination occurs.")
                raise AttributeError(e2)
        else:
            self.flex_share_up = self.max_capacity_up / self.max_demand

        self.cost_dsm_up = cost_dsm_up
        self.cost_dsm_down_shift = cost_dsm_down_shift
        self.cost_dsm_down_shed = cost_dsm_down_shed
        self.efficiency = efficiency
        self.recovery_time_shift = recovery_time_shift
        self.recovery_time_shed = recovery_time_shed
        self.shed_eligibility = shed_eligibility
        self.shift_eligibility = shift_eligibility

        # Check whether investment mode is active or not
        self.investment = kwargs.get("investment")
        self._invest_group = isinstance(self.investment, Investment)

    def _check_invest_attributes(self):
        if (self.investment is not None
            and (self.max_demand
                 or self.max_capacity_down
                 or self.max_capacity_up)
                is not None):
            e1 = (
                "If an investment object is defined, the invest variable "
                "replaces the max_demand, the max_capacity_down as well as\n"
                "the max_capacity_up values. Therefore, max_demand,\n"
                "max_capacity_up and max_capacity_down values should be 'None'.\n"
            )
            raise AttributeError(e1)

    def constraint_group(self):
        possible_approaches = ['DIW', 'oemof']

        if self.approach == possible_approaches[0]:
            if self.delay_time is None:
                raise ValueError('Please define: **delay_time'
                                 'is a mandatory parameter')
            if not self.shed_eligibility and not self.shift_eligibility:
                raise ValueError('At least one of **shed_eligibility'
                                 ' and **shift_eligibility must be True')
            if self.shed_eligibility:
                if self.recovery_time_shed is None:
                    raise ValueError('If unit is eligible for load shedding,'
                                     ' **recovery_time_shed must be defined')
            if self._invest_group is True:
                return SinkDSMDIWInvestmentBlock
            else:
                return SinkDSMDIWBlock

        elif self.approach == possible_approaches[1]:
            if self.shift_interval is None:
                raise ValueError('Please define: **shift_interval'
                                 ' is a mandatory parameter')
            if self._invest_group is True:
                return SinkDSMOemofInvestmentBlock
            else:
                return SinkDSMOemofBlock
        else:
            raise ValueError(
                'The "approach" must be one of the following set: '
                '"{}"'.format('" or "'.join(possible_approaches)))


class SinkDSMOemofBlock(SimpleBlock):
    r"""Constraints for SinkDSM with "oemof" approach

    **The following constraints are created for approach = 'oemof':**

    .. _SinkDSMInterval-equations:

    .. math::
        &
        (1) \quad \dot{E}_{t} = demand_{t} + DSM_{t}^{up} - DSM_{t}^{do}
        \quad \forall t \in \mathbb{T}\\
        &
        (2) \quad  DSM_{t}^{up} \leq E_{t}^{up} \quad \forall t \in
        \mathbb{T}\\
        &
        (3) \quad DSM_{t}^{do} \leq  E_{t}^{do} \quad \forall t \in
        \mathbb{T}\\
        &
        (4) \quad  \sum_{t=t_s}^{t_s+\tau} DSM_{t}^{up} \cdot \eta =
        \sum_{t=t_s}^{t_s+\tau} DSM_{t}^{do} \quad \forall t_s \in \{k
        \in \mathbb{T} \mid k \mod \tau = 0\} \\
        &


    **Table: Symbols and attribute names of variables and parameters**

        .. csv-table:: Variables (V) and Parameters (P)
            :header: "symbol", "attribute", "type", "explanation"
            :widths: 1, 1, 1, 1

            ":math:`DSM_{t}^{up}` ",":attr:`~SinkDSM.dsm_up` ","V", "DSM
            up shift"
            ":math:`DSM_{t}^{do}` ",":attr:`~SinkDSM.dsm_down` ","V","DSM
            down shift"
            ":math:`\dot{E}_{t}`",":attr:`~SinkDSM.inputs`","V", "Energy
            flowing in from electrical bus"
            ":math:`demand_{t}`",":attr:`demand[t]`","P", "Electrical demand
            series"
            ":math:`E_{t}^{do}`",":attr:`capacity_down[t]`","P", "Capacity
            DSM down shift capacity"
            ":math:`E_{t}^{up}`",":attr:`capacity_up[t]`","P", "Capacity
            DSM up shift "
            ":math:`\tau`",":attr:`~SinkDSM.shift_interval`","P", "Shift
            interval"
            ":math:`\eta`",":attr:`efficiency`","P", "Efficiency loss for
            load shifting processes"
            ":math:`\mathbb{T}` "," ","P", "Time steps"

    """
    CONSTRAINT_GROUP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _create(self, group=None):
        if group is None:
            return None

        m = self.parent_block()

        # for all DSM components get inflow from bus_elec
        for n in group:
            n.inflow = list(n.inputs)[0]

        #  ************* SETS *********************************

        # Set of DSM Components
        self.dsm = Set(initialize=[n for n in group])

        #  ************* VARIABLES *****************************

        # Variable load shift down
        self.dsm_do_shift = Var(self.dsm, m.TIMESTEPS, initialize=0,
                                within=NonNegativeReals)

        # Variable load shift up
        self.dsm_up = Var(self.dsm, m.TIMESTEPS, initialize=0,
                          within=NonNegativeReals)

        #  ************* CONSTRAINTS *****************************

        # Demand Production Relation
        def _input_output_relation_rule(block):
            """
            Relation between input data and pyomo variables.
            The actual demand after DSM.
            Generator Production == Demand_el +- DSM
            """
            for t in m.TIMESTEPS:
                for g in group:
                    # Generator loads directly from bus
                    lhs = m.flow[g.inflow, g, t]

                    # Demand + DSM_up - DSM_down
                    rhs = (g.demand[t] * g.max_demand
                           + self.dsm_up[g, t] - self.dsm_do_shift[g, t])

                    # add constraint
                    block.input_output_relation.add((g, t), (lhs == rhs))

        self.input_output_relation = Constraint(group, m.TIMESTEPS,
                                                noruleinit=True)
        self.input_output_relation_build = BuildAction(
            rule=_input_output_relation_rule)

        # Upper bounds relation
        def dsm_up_constraint_rule(block):
            """
            Realised upward load shift at time t has to be smaller than
            upward DSM capacity at time t.
            """

            for t in m.TIMESTEPS:
                for g in group:
                    # DSM up
                    lhs = self.dsm_up[g, t]
                    # Capacity dsm_up
                    rhs = g.capacity_up[t] * g.max_capacity_up

                    # add constraint
                    block.dsm_up_constraint.add((g, t), (lhs <= rhs))

        self.dsm_up_constraint = Constraint(group, m.TIMESTEPS,
                                            noruleinit=True)
        self.dsm_up_constraint_build = BuildAction(rule=dsm_up_constraint_rule)

        # Upper bounds relation
        def dsm_down_constraint_rule(block):
            """
            Realised downward load shift at time t has to be smaller than
            downward DSM capacity at time t.
            """

            for t in m.TIMESTEPS:
                for g in group:
                    # DSM down
                    lhs = self.dsm_do_shift[g, t]
                    # Capacity dsm_down
                    rhs = g.capacity_down[t] * g.max_capacity_down

                    # add constraint
                    block.dsm_down_constraint.add((g, t), (lhs <= rhs))

        self.dsm_down_constraint = Constraint(group, m.TIMESTEPS,
                                              noruleinit=True)
        self.dsm_down_constraint_build = BuildAction(
            rule=dsm_down_constraint_rule)

        def dsm_sum_constraint_rule(block):
            """
            Relation to compensate the total amount of positive
            and negative DSM in between the shift_interval.
            This constraint is building balance in full intervals starting
            with index 0. The last interval might not be full.
            """

            for g in group:
                intervals = range(m.TIMESTEPS[1],
                                  m.TIMESTEPS[-1],
                                  g.shift_interval)

                for interval in intervals:
                    if (interval + g.shift_interval - 1) \
                            > m.TIMESTEPS[-1]:
                        timesteps = range(interval,
                                          m.TIMESTEPS[-1] + 1)
                    else:
                        timesteps = range(interval, interval +
                                          g.shift_interval)

                    # DSM up/down
                    lhs = sum(self.dsm_up[g, tt]
                              for tt in timesteps) * g.efficiency
                    # value
                    rhs = sum(self.dsm_do_shift[g, tt]
                              for tt in timesteps)

                    # add constraint
                    block.dsm_sum_constraint.add((g, interval), (lhs == rhs))

        self.dsm_sum_constraint = Constraint(group, m.TIMESTEPS,
                                             noruleinit=True)
        self.dsm_sum_constraint_build = BuildAction(
            rule=dsm_sum_constraint_rule)

    def _objective_expression(self):
        """Adding cost terms for DSM activity to obj. function"""

        m = self.parent_block()

        dsm_cost = 0

        for t in m.TIMESTEPS:
            for g in self.dsm:
                dsm_cost += self.dsm_up[g, t] * g.cost_dsm_up
                dsm_cost += self.dsm_do_shift[g, t] * g.cost_dsm_down_shift

        self.cost = Expression(expr=dsm_cost)

        return self.cost


class SinkDSMOemofInvestmentBlock(SimpleBlock):
    r"""Constraints for SinkDSM with "oemof" approach

    **The following constraints are created for approach = 'oemof':**

    .. _SinkDSMInterval-equations:

    .. math::
        &
        (1) \quad \dot{E}_{t} = demand_{t} + DSM_{t}^{up} - DSM_{t}^{do}
        \quad \forall t \in \mathbb{T}\\
        &
        (2) \quad  DSM_{t}^{up} \leq E_{t}^{up} \quad \forall t \in
        \mathbb{T}\\
        &
        (3) \quad DSM_{t}^{do} \leq  E_{t}^{do} \quad \forall t \in
        \mathbb{T}\\
        &
        (4) \quad  \sum_{t=t_s}^{t_s+\tau} DSM_{t}^{up} \cdot \eta =
        \sum_{t=t_s}^{t_s+\tau} DSM_{t}^{do} \quad \forall t_s \in \{k
        \in \mathbb{T} \mid k \mod \tau = 0\} \\
        &


    **Table: Symbols and attribute names of variables and parameters**

        .. csv-table:: Variables (V) and Parameters (P)
            :header: "symbol", "attribute", "type", "explanation"
            :widths: 1, 1, 1, 1

            ":math:`DSM_{t}^{up}` ",":attr:`~SinkDSM.dsm_up` ","V", "DSM
            up shift"
            ":math:`DSM_{t}^{do}` ",":attr:`~SinkDSM.dsm_down` ","V","DSM
            down shift"
            ":math:`\dot{E}_{t}`",":attr:`~SinkDSM.inputs`","V", "Energy
            flowing in from electrical bus"
            ":math:`demand_{t}`",":attr:`demand[t]`","P", "Electrical demand
            series"
            ":math:`E_{t}^{do}`",":attr:`capacity_down[t]`","P", "Capacity
            DSM down shift capacity"
            ":math:`E_{t}^{up}`",":attr:`capacity_up[t]`","P", "Capacity
            DSM up shift "
            ":math:`\tau`",":attr:`~SinkDSM.shift_interval`","P", "Shift
            interval"
            ":math:`\eta`",":attr:`efficiency`","P", "Efficiency loss for
            load shifting processes"
            ":math:`\mathbb{T}` "," ","P", "Time steps"

    """
    CONSTRAINT_GROUP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _create(self, group=None):
        if group is None:
            return None

        m = self.parent_block()

        # for all DSM components get inflow from bus_elec
        for n in group:
            n.inflow = list(n.inputs)[0]

        #  ************* SETS *********************************

        # Set of DSM Components
        self.investdsm = Set(initialize=[n for n in group])

        #  ************* VARIABLES *****************************

        # Define bounds for investments in demand response
        def _dsm_investvar_bound_rule(block, g):
            """
            Rule definition to bound the
            invested demand response capacity `invest`.
            """
            return g.investment.minimum, g.investment.maximum

        # Investment in DR capacity
        self.invest = Var(self.investdsm,
                          within=NonNegativeReals,
                          bounds=_dsm_investvar_bound_rule)

        # Variable load shift down
        self.dsm_do_shift = Var(self.investdsm, m.TIMESTEPS, initialize=0,
                                within=NonNegativeReals)

        # Variable load shift up
        self.dsm_up = Var(self.investdsm, m.TIMESTEPS, initialize=0,
                          within=NonNegativeReals)

        #  ************* CONSTRAINTS *****************************

        # Demand Production Relation
        def _input_output_relation_rule(block):
            """
            Relation between input data and pyomo variables.
            The actual demand after DSM.
            Generator Production == Demand_el +- DSM
            """
            for t in m.TIMESTEPS:
                for g in group:
                    # Generator loads directly from bus
                    lhs = m.flow[g.inflow, g, t]

                    # Demand + DSM_up - DSM_down
                    rhs = (g.demand[t] * self.invest[g]
                           + self.dsm_up[g, t] - self.dsm_do_shift[g, t])

                    # add constraint
                    block.input_output_relation.add((g, t), (lhs == rhs))

        self.input_output_relation = Constraint(group, m.TIMESTEPS,
                                                noruleinit=True)
        self.input_output_relation_build = BuildAction(
            rule=_input_output_relation_rule)

        # Upper bounds relation
        def dsm_up_constraint_rule(block):
            """
            Realised upward load shift at time t has to be smaller than
            upward DSM capacity at time t.
            """

            for t in m.TIMESTEPS:
                for g in group:
                    # DSM up
                    lhs = self.dsm_up[g, t]
                    # Capacity dsm_up
                    rhs = g.capacity_up[t] * self.invest[g] * g.flex_share_up

                    # add constraint
                    block.dsm_up_constraint.add((g, t), (lhs <= rhs))

        self.dsm_up_constraint = Constraint(group, m.TIMESTEPS,
                                            noruleinit=True)
        self.dsm_up_constraint_build = BuildAction(rule=dsm_up_constraint_rule)

        # Upper bounds relation
        def dsm_down_constraint_rule(block):
            """
            Realised downward load shift at time t has to be smaller than
            downward DSM capacity at time t.
            """

            for t in m.TIMESTEPS:
                for g in group:
                    # DSM down
                    lhs = self.dsm_do_shift[g, t]
                    # Capacity dsm_down
                    rhs = g.capacity_down[t] * self.invest[g] * g.flex_share_down

                    # add constraint
                    block.dsm_down_constraint.add((g, t), (lhs <= rhs))

        self.dsm_down_constraint = Constraint(group, m.TIMESTEPS,
                                              noruleinit=True)
        self.dsm_down_constraint_build = BuildAction(
            rule=dsm_down_constraint_rule)

        def dsm_sum_constraint_rule(block):
            """
            Relation to compensate the total amount of positive
            and negative DSM in between the shift_interval.
            This constraint is building balance in full intervals starting
            with index 0. The last interval might not be full.
            """

            for g in group:
                intervals = range(m.TIMESTEPS[1],
                                  m.TIMESTEPS[-1],
                                  g.shift_interval)

                for interval in intervals:
                    if (interval + g.shift_interval - 1) \
                            > m.TIMESTEPS[-1]:
                        timesteps = range(interval,
                                          m.TIMESTEPS[-1] + 1)
                    else:
                        timesteps = range(interval, interval +
                                          g.shift_interval)

                    # DSM up/down
                    lhs = sum(self.dsm_up[g, tt]
                              for tt in timesteps) * g.efficiency
                    # value
                    rhs = sum(self.dsm_do_shift[g, tt]
                              for tt in timesteps)

                    # add constraint
                    block.dsm_sum_constraint.add((g, interval), (lhs == rhs))

        self.dsm_sum_constraint = Constraint(group, m.TIMESTEPS,
                                             noruleinit=True)
        self.dsm_sum_constraint_build = BuildAction(
            rule=dsm_sum_constraint_rule)

    def _objective_expression(self):
        """Adding cost terms for DSM activity to obj. function"""

        m = self.parent_block()

        investment_costs = 0
        variable_costs = 0

        for g in self.investdsm:
            if g.investment.ep_costs is not None:
                investment_costs += self.invest[g] * g.investment.ep_costs
            else:
                raise ValueError("Missing value for investment costs!")
            for t in m.TIMESTEPS:
                variable_costs += self.dsm_up[g, t] * g.cost_dsm_up
                variable_costs += self.dsm_do_shift[g, t] * g.cost_dsm_down_shift

        self.cost = Expression(expr=investment_costs + variable_costs)

        return self.cost


class SinkDSMDIWBlock(SimpleBlock):
    r"""Constraints for SinkDSM with "DIW" approach

    **The following constraints are created for approach = 'DIW':**

    .. _SinkDSMDelay-equations:

    .. math::


        &
        (1) \quad \dot{E}_{t} = demand_{t} + DSM_{t}^{up} -
        \sum_{tt=t-L}^{t+L} DSM_{t,tt}^{do}  \quad \forall t \in \mathbb{T} \\
        &
        (2) \quad DSM_{t}^{up} \cdot \eta = \sum_{tt=t-L}^{t+L} DSM_{t,tt}^{do}
        \quad \forall t \in \mathbb{T} \\
        &
        (3) \quad DSM_{t}^{up} \leq  E_{t}^{up} \quad \forall t \in
        \mathbb{T} \\
        &
        (4) \quad \sum_{t=tt-L}^{tt+L} DSM_{t,tt}^{do}  \leq E_{tt}^{do}
        \quad \forall tt \in \mathbb{T} \\
        &
        (5) \quad DSM_{tt}^{up}  + \sum_{t=tt-L}^{tt+L} DSM_{t,tt}^{do}
        \leq max \{ E_{tt}^{up}, E_{tt}^{do} \}\quad \forall tt \in \mathbb{T} \\
        &
        (6) \quad \sum_{tt=t}^{t+R-1} DSM_{tt}^{up} \leq E_{t}^{up} \cdot L
        \quad \forall t \in \mathbb{T} \\
        &



    **Table: Symbols and attribute names of variables and parameters**


        .. csv-table:: Variables (V) and Parameters (P)
            :header: "symbol", "attribute", "type", "explanation"
            :widths: 1, 1, 1, 1



            ":math:`DSM_{t}^{up}` ",":attr:`dsm_up[g,t]`", "V","DSM up
            shift (additional load) in hour t"
            ":math:`DSM_{t,tt}^{do}` ",":attr:`dsm_do_shift[g,t,tt]`","V","DSM down
            shift (less load) in hour tt to compensate for upwards shifts in hour t"
            ":math:`\dot{E}_{t}` ",":attr:`flow[g,t]`","V","Energy
            flowing in from electrical bus"
            ":math:`L`",":attr:`delay_time`","P", "Delay time for
            load shift"
            ":math:`demand_{t}` ",":attr:`demand[t]`","P","Electrical
            demand series"
            ":math:`E_{t}^{do}` ",":attr:`capacity_down[t]`","P","Capacity
            DSM down shift "
            ":math:`E_{t}^{up}` ", ":attr:`capacity_up[t]`", "P","Capacity
            ":math:`\eta`",":attr:`efficiency`","P", "Efficiency loss for
            load shifting processes"
            ":math:`\R`",":attr:`recovery_time_shift`","P", "Minimum time between the
            end of one load shifting process and the start of another"
            DSM up shift"
            ":math:`\mathbb{T}` "," ","P", "Time steps"


    """
    CONSTRAINT_GROUP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _create(self, group=None):
        if group is None:
            return None

        m = self.parent_block()

        # for all DSM components get inflow from bus_elec
        for n in group:
            n.inflow = list(n.inputs)[0]

        #  ************* SETS *********************************

        # Set of DSM Components
        self.dsm = Set(initialize=[g for g in group])

        #  ************* VARIABLES *****************************

        # Variable load shift down
        self.dsm_do_shift = Var(self.dsm, m.TIMESTEPS, m.TIMESTEPS, initialize=0,
                                within=NonNegativeReals)

        # Variable load shedding
        self.dsm_do_shed = Var(self.dsm, m.TIMESTEPS, initialize=0,
                               within=NonNegativeReals)

        # Variable load shift up
        self.dsm_up = Var(self.dsm, m.TIMESTEPS, initialize=0,
                          within=NonNegativeReals)

        #  ************* CONSTRAINTS *****************************

        def _shift_shed_vars_rule(block):
            """
            Force shifting resp. shedding variables to zero dependent
            on how boolean parameters for shift resp. shed eligibility
            are set.
            """
            for t in m.TIMESTEPS:
                for g in group:

                    if not g.shift_eligibility:
                        # Memo: By forcing dsm_do_shift for shifting to zero, dsm up should
                        # implicitly be forced to zero as well, since otherwhise,
                        # constraints below would not hold ...
                        lhs = self.dsm_do_shift[g, t]
                        rhs = 0

                        block.shift_shed_vars.add((g, t), (lhs == rhs))

                    if not g.shed_eligibility:
                        lhs = self.dsm_do_shed[g, t]
                        rhs = 0

                        block.shift_shed_vars.add((g, t), (lhs == rhs))

        self.shift_shed_vars = Constraint(group, m.TIMESTEPS,
                                          noruleinit=True)
        self.shift_shed_vars_build = BuildAction(
            rule=_shift_shed_vars_rule)

        # Demand Production Relation
        def _input_output_relation_rule(block):
            """
            Relation between input data and pyomo variables. The actual demand
            after DSM. Generator Production == Demand +- DSM
            """
            for t in m.TIMESTEPS:
                for g in group:

                    # first time steps: 0 + delay time
                    if t <= g.delay_time:

                        # Generator loads from bus
                        lhs = m.flow[g.inflow, g, t]
                        # Demand +- DSM
                        rhs = (g.demand[t] * g.max_demand + self.dsm_up[g, t]
                               - sum(
                                    self.dsm_do_shift[g, tt, t]
                                    for tt in range(t + g.delay_time + 1))
                               - self.dsm_do_shed[g, t])

                        # add constraint
                        block.input_output_relation.add((g, t), (lhs == rhs))

                    # main use case
                    elif g.delay_time < t <= \
                            m.TIMESTEPS[-1] - g.delay_time:

                        # Generator loads from bus
                        lhs = m.flow[g.inflow, g, t]
                        # Demand +- DSM
                        rhs = (g.demand[t] * g.max_demand + self.dsm_up[g, t]
                               - sum(
                                    self.dsm_do_shift[g, tt, t]
                                    for tt in range(t - g.delay_time,
                                                    t + g.delay_time + 1))
                               - self.dsm_do_shed[g, t])

                        # add constraint
                        block.input_output_relation.add((g, t), (lhs == rhs))

                    # last time steps: end - delay time
                    else:
                        # Generator loads from bus
                        lhs = m.flow[g.inflow, g, t]
                        # Demand +- DSM
                        rhs = (g.demand[t] * g.max_demand + self.dsm_up[g, t]
                               - sum(
                                    self.dsm_do_shift[g, tt, t]
                                    for tt in range(t - g.delay_time,
                                                    m.TIMESTEPS[-1] + 1))
                               - self.dsm_do_shed[g, t])

                        # add constraint
                        block.input_output_relation.add((g, t), (lhs == rhs))

        self.input_output_relation = Constraint(group, m.TIMESTEPS,
                                                noruleinit=True)
        self.input_output_relation_build = BuildAction(
            rule=_input_output_relation_rule)

        # Equation 7 (resp. 7')
        def dsm_up_down_constraint_rule(block):
            """
            Equation 7 (resp. 7') by Zerrahn, Schill:
            Every upward load shift has to be compensated by downward load
            shifts in a defined time frame. Slightly modified equations for
            the first and last time steps due to variable initialization.
            Efficiency value depicts possible energy losses through
            load shifiting (Equation 7').
            """

            for t in m.TIMESTEPS:
                for g in group:

                    # first time steps: 0 + delay time
                    if t <= g.delay_time:

                        # DSM up
                        lhs = self.dsm_up[g, t] * g.efficiency
                        # DSM down
                        rhs = sum(self.dsm_do_shift[g, t, tt]
                                  for tt in range(t + g.delay_time + 1))

                        # add constraint
                        block.dsm_updo_constraint.add((g, t), (lhs == rhs))

                    # main use case
                    elif g.delay_time < t <= \
                            m.TIMESTEPS[-1] - g.delay_time:

                        # DSM up
                        lhs = self.dsm_up[g, t] * g.efficiency
                        # DSM down
                        rhs = sum(self.dsm_do_shift[g, t, tt]
                                  for tt in range(t - g.delay_time,
                                                  t + g.delay_time + 1))

                        # add constraint
                        block.dsm_updo_constraint.add((g, t), (lhs == rhs))

                    # last time steps: end - delay time
                    else:

                        # DSM up
                        lhs = self.dsm_up[g, t] * g.efficiency
                        # DSM down
                        rhs = sum(self.dsm_do_shift[g, t, tt]
                                  for tt in range(t - g.delay_time,
                                                  m.TIMESTEPS[-1] + 1))

                        # add constraint
                        block.dsm_updo_constraint.add((g, t), (lhs == rhs))

        self.dsm_updo_constraint = Constraint(group, m.TIMESTEPS,
                                              noruleinit=True)
        self.dsm_updo_constraint_build = BuildAction(
            rule=dsm_up_down_constraint_rule)

        # Equation 8
        def dsm_up_constraint_rule(block):
            """
            Equation 8 by Zerrahn, Schill:
            Realised upward load shift at time t has to be smaller than
            upward DSM capacity at time t.
            """

            for t in m.TIMESTEPS:
                for g in group:
                    # DSM up
                    lhs = self.dsm_up[g, t]
                    # Capacity dsm_up
                    rhs = g.capacity_up[t] * g.max_capacity_up

                    # add constraint
                    block.dsm_up_constraint.add((g, t), (lhs <= rhs))

        self.dsm_up_constraint = Constraint(group, m.TIMESTEPS,
                                            noruleinit=True)
        self.dsm_up_constraint_build = BuildAction(rule=dsm_up_constraint_rule)

        # Equation 9 (modified)
        def dsm_do_constraint_rule(block):
            """
            Equation 9 by Zerrahn, Schill:
            Realised downward load shift at time t has to be smaller than
            downward DSM capacity at time t.
            """

            for tt in m.TIMESTEPS:
                for g in group:

                    if not g.shed_eligibility and g.shift_eligibility:

                        # first times steps: 0 + delay
                        if tt <= g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt + g.delay_time + 1))
                            # Capacity DSM down
                            rhs = g.capacity_down[tt] * g.max_capacity_down

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # main use case
                        elif g.delay_time < tt <= \
                                m.TIMESTEPS[-1] - g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     tt + g.delay_time + 1))
                            # Capacity DSM down
                            rhs = g.capacity_down[tt] * g.max_capacity_down

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # last time steps: end - delay time
                        else:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     m.TIMESTEPS[-1] + 1))
                            # Capacity DSM down
                            rhs = g.capacity_down[tt] * g.max_capacity_down

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                    # if shed eligibility (and shift_eligibility)
                    elif g.shed_eligibility and g.shift_eligibility:

                        # first times steps: 0 + delay
                        if tt <= g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt + g.delay_time + 1)) \
                                  + self.dsm_do_shed[g, tt]
                            # Capacity DSM down
                            rhs = g.capacity_down[tt] * g.max_capacity_down

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # main use case
                        elif g.delay_time < tt <= \
                                m.TIMESTEPS[-1] - g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     tt + g.delay_time + 1)) \
                                  + self.dsm_do_shed[g, tt]
                            # Capacity DSM down
                            rhs = g.capacity_down[tt] * g.max_capacity_down

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # last time steps: end - delay time
                        else:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     m.TIMESTEPS[-1] + 1)) \
                                  + self.dsm_do_shed[g, tt]
                            # Capacity DSM down
                            rhs = g.capacity_down[tt] * g.max_capacity_down

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                    # shed_eligibility only -> will be added later on
                    else:
                        pass

        self.dsm_do_constraint = Constraint(group, m.TIMESTEPS,
                                            noruleinit=True)
        self.dsm_do_constraint_build = BuildAction(
            rule=dsm_do_constraint_rule)

        # Equation 10
        def c2_constraint_rule(block):
            """
            Equation 10 by Zerrahn, Schill:
            The realised DSM up or down at time T has to be smaller than
            the maximum downward or upward capacity at time T. Therefore in
            total each DSM unit can only be shifted up OR down.
            """

            for tt in m.TIMESTEPS:
                for g in group:

                    # first times steps: 0 + delay time
                    if tt <= g.delay_time:

                        # DSM up/down
                        lhs = self.dsm_up[g, tt] + sum(
                            self.dsm_do_shift[g, t, tt]
                            for t in range(tt + g.delay_time + 1)) \
                              + self.dsm_do_shed[g, tt]
                        # max capacity at tt
                        rhs = max(g.capacity_up[tt] * g.max_capacity_up,
                                  g.capacity_down[tt] * g.max_capacity_down)

                        # add constraint
                        block.C2_constraint.add((g, tt), (lhs <= rhs))

                    elif g.delay_time < tt <= \
                            m.TIMESTEPS[-1] - g.delay_time:

                        # DSM up/down
                        lhs = self.dsm_up[g, tt] + sum(
                            self.dsm_do_shift[g, t, tt]
                            for t in range(tt - g.delay_time,
                                           tt + g.delay_time + 1)) \
                              + self.dsm_do_shed[g, tt]
                        # max capacity at tt
                        rhs = max(g.capacity_up[tt] * g.max_capacity_up,
                                  g.capacity_down[tt] * g.max_capacity_down)

                        # add constraint
                        block.C2_constraint.add((g, tt), (lhs <= rhs))

                    else:

                        # DSM up/down
                        lhs = self.dsm_up[g, tt] + sum(
                            self.dsm_do_shift[g, t, tt]
                            for t in range(tt - g.delay_time,
                                           m.TIMESTEPS[-1] + 1)) \
                              + self.dsm_do_shed[g, tt]
                        # max capacity at tt
                        rhs = max(g.capacity_up[tt] * g.max_capacity_up,
                                  g.capacity_down[tt] * g.max_capacity_down)

                        # add constraint
                        block.C2_constraint.add((g, tt), (lhs <= rhs))

        self.C2_constraint = Constraint(group, m.TIMESTEPS, noruleinit=True)
        self.C2_constraint_build = BuildAction(rule=c2_constraint_rule)

        def recovery_constraint_rule(block):
            """
            Equation 11 by Zerrahn, Schill:
            A recovery time is introduced to account for the fact that
            there may be some restrictions before the next load shift
            may take place. Rule is only applicable if a recovery time
            is defined.
            """

            for t in m.TIMESTEPS:
                for g in group:

                    # No need to build constraint if no recovery
                    # time is defined. Not quite sure, where to place this
                    # if condition ...
                    if g.recovery_time_shift not in [None, 0]:

                        # main use case
                        if t <= m.TIMESTEPS[-1] - g.recovery_time_shift:

                            # DSM up
                            lhs = sum(self.dsm_up[g, tt] for tt in
                                      range(t, t + g.recovery_time_shift))
                            # max energy shift for shifting process
                            rhs = (g.capacity_up[t] * g.max_capacity_up
                                   * g.delay_time * m.timeincrement[t])
                            # add constraint
                            block.recovery_constraint.add((g, t), (lhs <= rhs))

                        # last time steps: end - recovery time
                        else:

                            # DSM up
                            lhs = sum(self.dsm_up[g, tt] for tt in
                                      range(t, m.TIMESTEPS[-1] + 1))
                            # max energy shift for shifting process
                            rhs = (g.capacity_up[t] * g.max_capacity_up
                                   * g.delay_time * m.timeincrement[t])
                            # add constraint
                            block.recovery_constraint.add((g, t), (lhs <= rhs))

                    else:
                        pass  # return(Constraint.Skip)

        self.recovery_constraint = Constraint(group, m.TIMESTEPS,
                                              noruleinit=True)
        self.recovery_constraint_build = BuildAction(
            rule=recovery_constraint_rule)

        # Equation 9a from Zerrahn and Schill (2015b)
        def shed_limit_constraint_rule(block):
            """
            The following constraint is highly similar to equation 9a
            from Zerrahn and Schill (2015b): A recovery time for load
            shedding is introduced in order to limit the overall amount
            of shedded energy.
            """
            for t in m.TIMESTEPS:
                for g in group:

                    if g.shed_eligibility:

                        # main use case
                        if t <= m.TIMESTEPS[-1] - g.recovery_time_shed:

                            # DSM up
                            lhs = sum(self.dsm_do_shed[g, tt] for tt in
                                      range(t, t + g.recovery_time_shed))
                            # max energy shift for shifting process
                            rhs = (g.capacity_down[t] * g.max_capacity_down
                                   * g.shed_time * m.timeincrement[t])
                            # add constraint
                            block.shed_limit_constraint.add((g, t), (lhs <= rhs))

                        # last time steps: end - recovery time
                        else:

                            # DSM up
                            lhs = sum(self.dsm_do_shed[g, tt] for tt in
                                      range(t, m.TIMESTEPS[-1] + 1))
                            # max energy shift for shifting process
                            rhs = (g.capacity_down[t] * g.max_capacity_down
                                   * g.shed_time * m.timeincrement[t])
                            # add constraint
                            block.shed_limit_constraint.add((g, t), (lhs <= rhs))

                    else:
                        pass  # return(Constraint.Skip)

        self.shed_limit_constraint = Constraint(group, m.TIMESTEPS,
                                                noruleinit=True)
        self.shed_limit_constraint_build = BuildAction(
            rule=shed_limit_constraint_rule)

    def _objective_expression(self):
        """Adding cost terms for DSM activity to obj. function"""

        m = self.parent_block()

        dsm_cost = 0

        for t in m.TIMESTEPS:
            for g in self.dsm:
                dsm_cost += self.dsm_up[g, t] * g.cost_dsm_up
                dsm_cost += sum(self.dsm_do_shift[g, t, tt]
                                for tt in m.TIMESTEPS) * g.cost_dsm_down_shift \
                            + self.dsm_do_shed[g, t] * g.cost_dsm_down_shed

        self.cost = Expression(expr=dsm_cost)

        return self.cost


class SinkDSMDIWInvestmentBlock(SinkDSMDIWBlock):
    r"""Constraints for SinkDSM with "DIW" approach

    **The following constraints are created for approach = 'DIW':**

    .. _SinkDSMDelay-equations:

    .. math::


        &
        (1) \quad \dot{E}_{t} = demand_{t} + DSM_{t}^{up} -
        \sum_{tt=t-L}^{t+L} DSM_{t,tt}^{do}  \quad \forall t \in \mathbb{T} \\
        &
        (2) \quad DSM_{t}^{up} \cdot \eta = \sum_{tt=t-L}^{t+L} DSM_{t,tt}^{do}
        \quad \forall t \in \mathbb{T} \\
        &
        (3) \quad DSM_{t}^{up} \leq  E_{t}^{up} \quad \forall t \in
        \mathbb{T} \\
        &
        (4) \quad \sum_{t=tt-L}^{tt+L} DSM_{t,tt}^{do}  \leq E_{tt}^{do}
        \quad \forall tt \in \mathbb{T} \\
        &
        (5) \quad DSM_{tt}^{up}  + \sum_{t=tt-L}^{tt+L} DSM_{t,tt}^{do}
        \leq max \{ E_{tt}^{up}, E_{tt}^{do} \}\quad \forall tt \in \mathbb{T} \\
        &
        (6) \quad \sum_{tt=t}^{t+R-1} DSM_{tt}^{up} \leq E_{t}^{up} \cdot L
        \quad \forall t \in \mathbb{T} \\
        &



    **Table: Symbols and attribute names of variables and parameters**


        .. csv-table:: Variables (V) and Parameters (P)
            :header: "symbol", "attribute", "type", "explanation"
            :widths: 1, 1, 1, 1



            ":math:`DSM_{t}^{up}` ",":attr:`dsm_up[g,t]`", "V","DSM up
            shift (additional load) in hour t"
            ":math:`DSM_{t,tt}^{do}` ",":attr:`dsm_do_shift[g,t,tt]`","V","DSM down
            shift (less load) in hour tt to compensate for upwards shifts in hour t"
            ":math:`\dot{E}_{t}` ",":attr:`flow[g,t]`","V","Energy
            flowing in from electrical bus"
            ":math:`L`",":attr:`delay_time`","P", "Delay time for
            load shift"
            ":math:`demand_{t}` ",":attr:`demand[t]`","P","Electrical
            demand series"
            ":math:`E_{t}^{do}` ",":attr:`capacity_down[t]`","P","Capacity
            DSM down shift "
            ":math:`E_{t}^{up}` ", ":attr:`capacity_up[t]`", "P","Capacity
            ":math:`\eta`",":attr:`efficiency`","P", "Efficiency loss for
            load shifting processes"
            ":math:`\R`",":attr:`recovery_time_shift`","P", "Minimum time between the
            end of one load shifting process and the start of another"
            DSM up shift"
            ":math:`\mathbb{T}` "," ","P", "Time steps"


    """
    CONSTRAINT_GROUP = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _create(self, group=None):
        if group is None:
            return None

        m = self.parent_block()

        # for all DSM components get inflow from bus_elec
        for n in group:
            n.inflow = list(n.inputs)[0]

        #  ************* SETS *********************************

        # Set of DSM Components
        self.investdsm = Set(initialize=[g for g in group])

        #  ************* VARIABLES *****************************

        # Define bounds for investments in demand response
        def _dsm_investvar_bound_rule(block, g):
            """
            Rule definition to bound the
            invested demand response capacity `invest`.
            """
            return g.investment.minimum, g.investment.maximum

        # Investment in DR capacity
        self.invest = Var(self.investdsm,
                          within=NonNegativeReals,
                          bounds=_dsm_investvar_bound_rule)

        # Variable load shift down
        self.dsm_do_shift = Var(self.investdsm, m.TIMESTEPS, m.TIMESTEPS, initialize=0,
                                within=NonNegativeReals)

        # Variable load shedding
        self.dsm_do_shed = Var(self.investdsm, m.TIMESTEPS, initialize=0,
                               within=NonNegativeReals)

        # Variable load shift up
        self.dsm_up = Var(self.investdsm, m.TIMESTEPS, initialize=0,
                          within=NonNegativeReals)

        #  ************* CONSTRAINTS *****************************

        def _shift_shed_vars_rule(block):
            """
            Force shifting resp. shedding variables to zero dependent
            on how boolean parameters for shift resp. shed eligibility
            are set.
            """
            for t in m.TIMESTEPS:
                for g in group:

                    if not g.shift_eligibility:
                        # Memo: By forcing dsm_do_shift for shifting to zero, dsm up should
                        # implicitly be forced to zero as well, since otherwhise,
                        # constraints below would not hold ...
                        lhs = self.dsm_do_shift[g, t]
                        rhs = 0

                        block.shift_shed_vars.add((g, t), (lhs == rhs))

                    if not g.shed_eligibility:
                        lhs = self.dsm_do_shed[g, t]
                        rhs = 0

                        block.shift_shed_vars.add((g, t), (lhs == rhs))

        self.shift_shed_vars = Constraint(group, m.TIMESTEPS,
                                          noruleinit=True)
        self.shift_shed_vars_build = BuildAction(
            rule=_shift_shed_vars_rule)

        # Demand Production Relation
        def _input_output_relation_rule(block):
            """
            Relation between input data and pyomo variables. The actual demand
            after DSM. Generator Production == Demand +- DSM
            """
            for t in m.TIMESTEPS:
                for g in group:

                    # first time steps: 0 + delay time
                    if t <= g.delay_time:

                        # Generator loads from bus
                        lhs = m.flow[g.inflow, g, t]
                        # Demand +- DSM
                        rhs = (g.demand[t] * self.invest[g] + self.dsm_up[g, t]
                               - sum(
                                    self.dsm_do_shift[g, tt, t]
                                    for tt in range(t + g.delay_time + 1))
                               - self.dsm_do_shed[g, t])

                        # add constraint
                        block.input_output_relation.add((g, t), (lhs == rhs))

                    # main use case
                    elif g.delay_time < t <= \
                            m.TIMESTEPS[-1] - g.delay_time:

                        # Generator loads from bus
                        lhs = m.flow[g.inflow, g, t]
                        # Demand +- DSM
                        rhs = (g.demand[t] * self.invest[g] + self.dsm_up[g, t]
                               - sum(
                                    self.dsm_do_shift[g, tt, t]
                                    for tt in range(t - g.delay_time,
                                                    t + g.delay_time + 1))
                               - self.dsm_do_shed[g, t])

                        # add constraint
                        block.input_output_relation.add((g, t), (lhs == rhs))

                    # last time steps: end - delay time
                    else:
                        # Generator loads from bus
                        lhs = m.flow[g.inflow, g, t]
                        # Demand +- DSM
                        rhs = (g.demand[t] * self.invest[g] + self.dsm_up[g, t]
                               - sum(
                                    self.dsm_do_shift[g, tt, t]
                                    for tt in range(t - g.delay_time,
                                                    m.TIMESTEPS[-1] + 1))
                               - self.dsm_do_shed[g, t])

                        # add constraint
                        block.input_output_relation.add((g, t), (lhs == rhs))

        self.input_output_relation = Constraint(group, m.TIMESTEPS,
                                                noruleinit=True)
        self.input_output_relation_build = BuildAction(
            rule=_input_output_relation_rule)

        # Equation 7 (resp. 7')
        def dsm_up_down_constraint_rule(block):
            """
            Equation 7 (resp. 7') by Zerrahn, Schill:
            Every upward load shift has to be compensated by downward load
            shifts in a defined time frame. Slightly modified equations for
            the first and last time steps due to variable initialization.
            Efficiency value depicts possible energy losses through
            load shifiting (Equation 7').
            """

            for t in m.TIMESTEPS:
                for g in group:

                    # first time steps: 0 + delay time
                    if t <= g.delay_time:

                        # DSM up
                        lhs = self.dsm_up[g, t] * g.efficiency
                        # DSM down
                        rhs = sum(self.dsm_do_shift[g, t, tt]
                                  for tt in range(t + g.delay_time + 1))

                        # add constraint
                        block.dsm_updo_constraint.add((g, t), (lhs == rhs))

                    # main use case
                    elif g.delay_time < t <= \
                            m.TIMESTEPS[-1] - g.delay_time:

                        # DSM up
                        lhs = self.dsm_up[g, t] * g.efficiency
                        # DSM down
                        rhs = sum(self.dsm_do_shift[g, t, tt]
                                  for tt in range(t - g.delay_time,
                                                  t + g.delay_time + 1))

                        # add constraint
                        block.dsm_updo_constraint.add((g, t), (lhs == rhs))

                    # last time steps: end - delay time
                    else:

                        # DSM up
                        lhs = self.dsm_up[g, t] * g.efficiency
                        # DSM down
                        rhs = sum(self.dsm_do_shift[g, t, tt]
                                  for tt in range(t - g.delay_time,
                                                  m.TIMESTEPS[-1] + 1))

                        # add constraint
                        block.dsm_updo_constraint.add((g, t), (lhs == rhs))

        self.dsm_updo_constraint = Constraint(group, m.TIMESTEPS,
                                              noruleinit=True)
        self.dsm_updo_constraint_build = BuildAction(
            rule=dsm_up_down_constraint_rule)

        # Equation 8
        def dsm_up_constraint_rule(block):
            """
            Equation 8 by Zerrahn, Schill:
            Realised upward load shift at time t has to be smaller than
            upward DSM capacity at time t.
            """

            for t in m.TIMESTEPS:
                for g in group:
                    # DSM up
                    lhs = self.dsm_up[g, t]
                    # Capacity dsm_up
                    rhs = g.capacity_up[t] * self.invest[g] * g.flex_share_up

                    # add constraint
                    block.dsm_up_constraint.add((g, t), (lhs <= rhs))

        self.dsm_up_constraint = Constraint(group, m.TIMESTEPS,
                                            noruleinit=True)
        self.dsm_up_constraint_build = BuildAction(rule=dsm_up_constraint_rule)

        # Equation 9 (modified)
        def dsm_do_constraint_rule(block):
            """
            Equation 9 by Zerrahn, Schill:
            Realised downward load shift at time t has to be smaller than
            downward DSM capacity at time t.
            """

            for tt in m.TIMESTEPS:
                for g in group:

                    if not g.shed_eligibility and g.shift_eligibility:

                        # first times steps: 0 + delay
                        if tt <= g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt + g.delay_time + 1))
                            # Capacity DSM down
                            rhs = (g.capacity_down[tt] * self.invest[g]
                                   * g.flex_share_down)

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # main use case
                        elif g.delay_time < tt <= \
                                m.TIMESTEPS[-1] - g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     tt + g.delay_time + 1))
                            # Capacity DSM down
                            rhs = (g.capacity_down[tt] * self.invest[g]
                                   * g.flex_share_down)

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # last time steps: end - delay time
                        else:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     m.TIMESTEPS[-1] + 1))
                            # Capacity DSM down
                            rhs = (g.capacity_down[tt] * self.invest[g]
                                   * g.flex_share_down)

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                    # if shed eligibility (and shift_eligibility)
                    elif g.shed_eligibility and g.shift_eligibility:

                        # first times steps: 0 + delay
                        if tt <= g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt + g.delay_time + 1)) \
                                  + self.dsm_do_shed[g, tt]
                            # Capacity DSM down
                            rhs = (g.capacity_down[tt] * self.invest[g]
                                   * g.flex_share_down)

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # main use case
                        elif g.delay_time < tt <= \
                                m.TIMESTEPS[-1] - g.delay_time:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     tt + g.delay_time + 1)) \
                                  + self.dsm_do_shed[g, tt]
                            # Capacity DSM down
                            rhs = (g.capacity_down[tt] * self.invest[g]
                                   * g.flex_share_down)

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                        # last time steps: end - delay time
                        else:

                            # DSM down
                            lhs = sum(self.dsm_do_shift[g, t, tt]
                                      for t in range(tt - g.delay_time,
                                                     m.TIMESTEPS[-1] + 1)) \
                                  + self.dsm_do_shed[g, tt]
                            # Capacity DSM down
                            rhs = (g.capacity_down[tt] * self.invest[g]
                                   * g.flex_share_down)

                            # add constraint
                            block.dsm_do_constraint.add((g, tt), (lhs <= rhs))

                    # shed_eligibility only -> will be added later on
                    else:
                        pass

        self.dsm_do_constraint = Constraint(group, m.TIMESTEPS,
                                            noruleinit=True)
        self.dsm_do_constraint_build = BuildAction(
            rule=dsm_do_constraint_rule)

        # Equation 10
        def c2_constraint_rule(block):
            """
            Equation 10 by Zerrahn, Schill:
            The realised DSM up or down at time T has to be smaller than
            the maximum downward or upward capacity at time T. Therefore in
            total each DSM unit can only be shifted up OR down.
            """

            for tt in m.TIMESTEPS:
                for g in group:

                    # first times steps: 0 + delay time
                    if tt <= g.delay_time:

                        # DSM up/down
                        lhs = self.dsm_up[g, tt] + sum(
                            self.dsm_do_shift[g, t, tt]
                            for t in range(tt + g.delay_time + 1)) \
                              + self.dsm_do_shed[g, tt]
                        # max capacity at tt
                        rhs = max(g.capacity_up[tt] * g.flex_share_up,
                                  g.capacity_down[tt] * g.flex_share_down) * self.invest[g]

                        # add constraint
                        block.C2_constraint.add((g, tt), (lhs <= rhs))

                    elif g.delay_time < tt <= \
                            m.TIMESTEPS[-1] - g.delay_time:

                        # DSM up/down
                        lhs = self.dsm_up[g, tt] + sum(
                            self.dsm_do_shift[g, t, tt]
                            for t in range(tt - g.delay_time,
                                           tt + g.delay_time + 1)) \
                              + self.dsm_do_shed[g, tt]
                        # max capacity at tt
                        rhs = max(g.capacity_up[tt] * g.flex_share_up,
                                  g.capacity_down[tt] * g.flex_share_down) * self.invest[g]

                        # add constraint
                        block.C2_constraint.add((g, tt), (lhs <= rhs))

                    else:

                        # DSM up/down
                        lhs = self.dsm_up[g, tt] + sum(
                            self.dsm_do_shift[g, t, tt]
                            for t in range(tt - g.delay_time,
                                           m.TIMESTEPS[-1] + 1)) \
                              + self.dsm_do_shed[g, tt]
                        # max capacity at tt
                        rhs = max(g.capacity_up[tt] * g.flex_share_up,
                                  g.capacity_down[tt] * g.flex_share_down) * self.invest[g]

                        # add constraint
                        block.C2_constraint.add((g, tt), (lhs <= rhs))

        self.C2_constraint = Constraint(group, m.TIMESTEPS, noruleinit=True)
        self.C2_constraint_build = BuildAction(rule=c2_constraint_rule)

        def recovery_constraint_rule(block):
            """
            Equation 11 by Zerrahn, Schill:
            A recovery time is introduced to account for the fact that
            there may be some restrictions before the next load shift
            may take place. Rule is only applicable if a recovery time
            is defined.
            """

            for t in m.TIMESTEPS:
                for g in group:

                    # No need to build constraint if no recovery
                    # time is defined. Not quite sure, where to place this
                    # if condition ...
                    if g.recovery_time_shift not in [None, 0]:

                        # main use case
                        if t <= m.TIMESTEPS[-1] - g.recovery_time_shift:

                            # DSM up
                            lhs = sum(self.dsm_up[g, tt] for tt in
                                      range(t, t + g.recovery_time_shift))
                            # max energy shift for shifting process
                            rhs = (g.capacity_up[t] * self.invest[g]
                                   * g.flex_share_up * g.delay_time
                                   * m.timeincrement[t])
                            # add constraint
                            block.recovery_constraint.add((g, t), (lhs <= rhs))

                        # last time steps: end - recovery time
                        else:

                            # DSM up
                            lhs = sum(self.dsm_up[g, tt] for tt in
                                      range(t, m.TIMESTEPS[-1] + 1))
                            # max energy shift for shifting process
                            rhs = (g.capacity_up[t] * self.invest[g]
                                   * g.flex_share_up * g.delay_time
                                   * m.timeincrement[t])
                            # add constraint
                            block.recovery_constraint.add((g, t), (lhs <= rhs))

                    else:
                        pass  # return(Constraint.Skip)

        self.recovery_constraint = Constraint(group, m.TIMESTEPS,
                                              noruleinit=True)
        self.recovery_constraint_build = BuildAction(
            rule=recovery_constraint_rule)

        # Equation 9a from Zerrahn and Schill (2015b)
        def shed_limit_constraint_rule(block):
            """
            The following constraint is highly similar to equation 9a
            from Zerrahn and Schill (2015b): A recovery time for load
            shedding is introduced in order to limit the overall amount
            of shedded energy.
            """
            for t in m.TIMESTEPS:
                for g in group:

                    if g.shed_eligibility:

                        # main use case
                        if t <= m.TIMESTEPS[-1] - g.recovery_time_shed:

                            # DSM up
                            lhs = sum(self.dsm_do_shed[g, tt] for tt in
                                      range(t, t + g.recovery_time_shed))
                            # max energy shift for shifting process
                            rhs = (g.capacity_down[t] * self.invest[g]
                                   * g.flex_share_down * g.shed_time
                                   * m.timeincrement[t])
                            # add constraint
                            block.shed_limit_constraint.add((g, t), (lhs <= rhs))

                        # last time steps: end - recovery time
                        else:

                            # DSM up
                            lhs = sum(self.dsm_do_shed[g, tt] for tt in
                                      range(t, m.TIMESTEPS[-1] + 1))
                            # max energy shift for shifting process
                            rhs = (g.capacity_down[t] * self.invest[g]
                                   * g.flex_share_down * g.shed_time
                                   * m.timeincrement[t])
                            # add constraint
                            block.shed_limit_constraint.add((g, t), (lhs <= rhs))

                    else:
                        pass  # return(Constraint.Skip)

        self.shed_limit_constraint = Constraint(group, m.TIMESTEPS,
                                                noruleinit=True)
        self.shed_limit_constraint_build = BuildAction(
            rule=shed_limit_constraint_rule)

    def _objective_expression(self):
        """Adding cost terms for DSM activity to obj. function"""

        m = self.parent_block()

        investment_costs = 0
        variable_costs = 0

        for g in self.investdsm:
            if g.investment.ep_costs is not None:
                investment_costs += self.invest[g] * g.investment.ep_costs
            else:
                raise ValueError("Missing value for investment costs!")

            for t in m.TIMESTEPS:
                variable_costs += self.dsm_up[g, t] * g.cost_dsm_up
                variable_costs += (sum(self.dsm_do_shift[g, t, tt]
                                      for tt in m.TIMESTEPS) * g.cost_dsm_down_shift
                                  + self.dsm_do_shed[g, t] * g.cost_dsm_down_shed)

        self.cost = Expression(expr=investment_costs + variable_costs)

        return self.cost
