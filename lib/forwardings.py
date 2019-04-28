import numpy as np
from lib.node import LndNode
import _settings

import logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

np.warnings.filterwarnings('ignore')


class ForwardingAnalyzer(object):
    """
    Analyzes forwardings for single channels.
    """
    def __init__(self, node):
        self.forwarding_events = node.get_forwarding_events()

        self.channels = {}
        self.total_forwarding_amount_sat = 0
        self.total_forwarding_fees_msat = 0
        self.forwardings = 0
        self.cumulative_effective_fee = 0
        self.timestamp_first_send = 1E10  # somewhere in future
        self.timestamp_last_send = 0  # at beginning of time
        self.max_time_interval = None

    def initialize_forwarding_data(self, time_start, time_end):
        """
        Initializes the channel statistics objects with data from the forwardings.
        :param time_start: time interval start, unix timestamp
        :param time_end: time interval end, unix timestamp
        """
        for f in self.forwarding_events:
            if time_start < f['timestamp'] < time_end:
                # make a dictionary entry for unknown channels
                channel_id_in = f['chan_id_in']
                channel_id_out = f['chan_id_out']

                if channel_id_in not in self.channels.keys():
                    self.channels[channel_id_in] = ChannelStatistics(channel_id_in)
                if channel_id_out not in self.channels.keys():
                    self.channels[channel_id_out] = ChannelStatistics(channel_id_out)

                self.channels[channel_id_in].inward_forwardings.append(f['amt_in'])
                self.channels[channel_id_out].outward_forwardings.append(f['amt_out'])
                self.channels[channel_id_out].absolute_fees.append(f['fee_msat'])
                self.channels[channel_id_out].effective_fees.append(f['effective_fee'])
                self.channels[channel_id_out].timestamps.append(f['timestamp'])

                self.total_forwarding_amount_sat += f['amt_in']
                self.total_forwarding_fees_msat += f['fee_msat']
                self.forwardings += 1
                self.cumulative_effective_fee += f['effective_fee']

    def get_forwarding_statistics_channels(self):
        """
        Prepares the forwarding statistics for each channel.
        :return: dict: statistics with channel_id as keys
        """
        channel_statistics = {}

        for k, c in self.channels.items():

            try:
                timestamp_first_send = min(c.timestamps)
                if self.timestamp_first_send > timestamp_first_send:
                    self.timestamp_first_send = timestamp_first_send
            except ValueError:
                pass
            try:
                timestamp_last_send = max(c.timestamps)
                if self.timestamp_last_send < timestamp_last_send:
                    self.timestamp_last_send = timestamp_last_send
            except ValueError:
                pass

            channel_statistics[k] = {
                'effective_fee': c.effective_fee(),
                'fees_total': c.fees_total(),
                'flow_direction': c.flow_direction(),
                'mean_forwarding_in': c.mean_forwarding_in(),
                'mean_forwarding_out': c.mean_forwarding_out(),
                'median_forwarding_in': c.median_forwarding_in(),
                'median_forwarding_out': c.median_forwarding_out(),
                'number_forwardings': c.number_forwardings(),
                'largest_forwarding_amount_in': c.largest_forwarding_amount_in(),
                'largest_forwarding_amount_out': c.largest_forwarding_amount_out(),
                'total_forwarding_in': c.total_forwarding_in(),
                'total_forwarding_out': c.total_forwarding_out(),
            }
        # determine the time interval starting with the first forwarding to the last forwarding in the analyzed
        # time interval determined by time_start and time_end
        self.max_time_interval = (self.timestamp_last_send - self.timestamp_first_send) / (24 * 60 * 60)
        return channel_statistics


class ChannelStatistics(object):
    """
    Functionality to analyze the forwardings of a single channel.
    """
    def __init__(self, channel_id):
        self.channel_id = channel_id

        self.inward_forwardings = []
        self.outward_forwardings = []

        self.timestamps = []
        self.absolute_fees = []
        self.effective_fees = []

    def total_forwarding_in(self):
        return sum(self.inward_forwardings)

    def total_forwarding_out(self):
        return sum(self.outward_forwardings)

    def mean_forwarding_in(self):
        return np.mean(self.inward_forwardings)

    def mean_forwarding_out(self):
        return np.mean(self.outward_forwardings)

    def median_forwarding_in(self):
        return np.median(self.inward_forwardings)

    def median_forwarding_out(self):
        return np.median(self.outward_forwardings)

    def fees_total(self):
        return sum(self.absolute_fees)

    def effective_fee(self):
        return np.mean(self.effective_fees)

    def largest_forwarding_amount_out(self):
        return max(self.outward_forwardings, default=float('nan'))

    def largest_forwarding_amount_in(self):
        return max(self.inward_forwardings, default=float('nan'))

    def flow_direction(self):
        total_in = self.total_forwarding_in()
        total_out = self.total_forwarding_out()
        return -((float(total_in) / (total_in + total_out)) - 0.5) / 0.5

    def number_forwardings(self):
        return len(self.inward_forwardings) + len(self.outward_forwardings)


def get_forwarding_statistics_channels(node, time_interval_start, time_interval_end):
    """
    Joins data from listchannels and fwdinghistory to have a extended information about a channel.
    :param node: :class:`lib.node.Node`
    :param time_interval_start: unix timestamp
    :param time_interval_end: unix timestamp
    :return: dict of channel information with channel_id as keys
    """
    forwarding_analyzer = ForwardingAnalyzer(node)
    forwarding_analyzer.initialize_forwarding_data(time_interval_start, time_interval_end)

    statistics = forwarding_analyzer.get_forwarding_statistics_channels()  # dict with channel_id keys
    logger.debug(f"Time interval (between first and last forwarding) is "
                 f"{forwarding_analyzer.max_time_interval:6.2f} days.")
    # join the two data sets:
    channels = node.get_unbalanced_channels(unbalancedness_greater_than=0.0)

    for c in channels:
        try:  # channel forwarding statistics exists
            channel_statistics = statistics[c['chan_id']]
            c['fees_total'] = channel_statistics['fees_total']
            c['fees_total_per_week'] = channel_statistics['fees_total'] / (forwarding_analyzer.max_time_interval / 7)
            c['flow_direction'] = channel_statistics['flow_direction']
            c['median_forwarding_in'] = channel_statistics['median_forwarding_in']
            c['median_forwarding_out'] = channel_statistics['median_forwarding_out']
            c['number_forwardings'] = channel_statistics['number_forwardings']
            c['largest_forwarding_amount_in'] = channel_statistics['largest_forwarding_amount_in']
            c['largest_forwarding_amount_out'] = channel_statistics['largest_forwarding_amount_out']
            c['total_forwarding_in'] = channel_statistics['total_forwarding_in']
            c['total_forwarding_out'] = channel_statistics['total_forwarding_out']
            # action required if flow same direction as unbalancedness
            if c['unbalancedness'] * c['flow_direction'] > 0 and abs(
                    c['unbalancedness']) > _settings.UNBALANCED_CHANNEL:
                c['rebalance_required'] = True
            else:
                c['rebalance_required'] = False

        except KeyError:  # no forwarding statistics on channel is available
            c['fees_total'] = 0
            c['fees_total_per_week'] = 0
            c['flow_direction'] = float('nan')
            c['median_forwarding_out'] = float('nan')
            c['median_forwarding_in'] = float('nan')
            c['number_forwardings'] = 0
            c['largest_forwarding_amount_in'] = float('nan')
            c['largest_forwarding_amount_out'] = float('nan')
            c['total_forwarding_in'] = float('nan')
            c['total_forwarding_out'] = float('nan')
            # action required if flow same direction as unbalancedness
            if abs(c['unbalancedness']) > _settings.UNBALANCED_CHANNEL:
                c['rebalance_required'] = True
            else:
                c['rebalance_required'] = False
    return channels


if __name__ == '__main__':
    nd = LndNode()
    fa = ForwardingAnalyzer(nd)
    fa.initialize_forwarding_data(0, 0)
    stats = fa.get_forwarding_statistics_channels()
    stats = sorted(stats.items(), key=lambda x: x[1]['fees_total'])
    for s in stats:
        print(s)