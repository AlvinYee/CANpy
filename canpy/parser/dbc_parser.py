__author__ = "Stefan Hölzl"

import re

from canpy.can_bus import CANBus, CANNode, CANMessage, CANSignal
from canpy.can_bus.can_attribute import *


class DBCParser(object):
    """Parses a DBC-file.
    Follows docs/DBC_Specification.md"""
    def __init__(self):
        """Initializes the object"""
        self._mode = ('NORMAL', None)
        self._canbus = CANBus()

        self._keywords = {'VERSION': self._parse_version,
                          'BU_':     self._parse_nodes,
                          'BO_':     self._parse_message,
                          'SG_':     self._parse_signal,
                          'CM_':     self._parse_description,
                          'BS_':     self._parse_bus_configuration,
                          'BA_DEF_': self._parse_attribute_definition,
                         }
        self._force_parser = False

    # Method definitions
    def parse_file(self, file_name):
        """Parses a dbc file

        Args:
            file_name: Name of the file to parse.
        Returns:
            CANDB object
        """
        self._canbus = CANBus()
        with open(file_name, 'r') as dbc_fh:
            for line in dbc_fh:
                self._parse_line(line.strip())
        return self._canbus

    def _parse_line(self, line):
        """Parses one line of a dbc file and updates the CANDB

        Args:
            line: One line of a dbc file as string
        Raises:
            RuntimeError: If signal description is not in a message block
        """
        if self._force_parser:
            self._force_parser(line)
        else:
            for key, parse_function in self._keywords.items():
                if line.startswith(key):
                    parse_function(line)

    def _parse_version(self, version_str):
        """Parses a version string

        Args:
            version_str: String containing version informations
        Returns:
            Version from the verstion string
        """
        reg = re.search('VERSION\s+"(?P<version>\S+)"', version_str)
        self._canbus.version = reg.group('version')

    def _parse_nodes(self, nodes_str):
        """Parses a nodes string

        Args:
            nodes_str: String containing nodes informations
        Returns:
            List with all the node names
        """
        reg = re.search('BU_\s*:\s*(?P<nodes>.+)\s*', nodes_str)
        node_names_str = re.sub('\s+', ' ', reg.group('nodes')).strip()
        for node_name in node_names_str.split(' '):
            self._canbus.add_node(CANNode(node_name))

    def _parse_message(self, message_str):
        """Parses a message string

        Args:
            message_str: String with message informations
        Returns:
            Namedtuple with can_id, name, length and sender name of the message
        """
        reg = re.search('BO_\s+(?P<can_id>\d+)\s+(?P<name>\S+)\s*:\s*(?P<length>\d+)\s+(?P<sender>\S+)', message_str)
        message = CANMessage(int(reg.group('can_id')), reg.group('name').strip(), int(reg.group('length')))
        self._canbus.nodes[reg.group('sender').strip()].add_message(message)
        self._mode = ('MESSAGE', message)

    def _parse_signal(self, signal_str):
        """Parses a signal string

        Args:
            signal_str: String with signal informations
        Returns:
            Namedtuple with the signal informations
        """
        pattern  = 'SG_\s+(?P<name>\S+)\s*(?P<is_multipexer>M)?(?P<multiplexer_id>m\d+)?\s*:\s*'
        pattern += '(?P<start_bit>\d+)\|(?P<length>\d+)\@(?P<endianness>[0|1])(?P<sign>[\+|\-])\s*'
        pattern += '\(\s*(?P<factor>\S+)\s*,\s*(?P<offset>\S+)\s*\)\s*\[\s*(?P<min_value>\S+)\s*\|\s*(?P<max_value>\S+)\s*\]'
        pattern += '\s*"(?P<unit>\S*)"\s+(?P<receivers>.+)'
        reg = re.search(pattern, signal_str)

        little_endian = True if reg.group('endianness').strip() == '1' else False
        signed = True if reg.group('sign').strip() == '-' else False
        receivers = [receiver.strip() for receiver in re.sub('\s+', ' ', reg.group('receivers')).strip().split(' ')]
        is_multiplexer = True if reg.group('is_multipexer') else False
        multiplexer_id = int(reg.group('multiplexer_id').strip()[1:]) if reg.group('multiplexer_id') else None

        if self._mode[0] != 'MESSAGE':
            raise RuntimeError('Signal description not in message block')
        signal = CANSignal(name=reg.group('name').strip(), start_bit=int(reg.group('start_bit')),
                           length=int(reg.group('length')), little_endian=little_endian, signed=signed,
                           factor=float(reg.group('factor')), offset=float(reg.group('offset')),
                           value_min=float(reg.group('min_value')), value_max=float(reg.group('max_value')),
                           unit=reg.group('unit').strip(), is_multiplexer=is_multiplexer, multiplexer_id=multiplexer_id)
        for node_name in receivers:
            node = self._canbus.nodes[node_name]
            signal.add_receiver(node)
        self._mode[1].add_signal(signal)

    def _parse_description(self, desc_str):
        """Parses a description string

        Args:
            desc_str: String with description informations
        Returns:
            Namedtuple with value, type and identifier of the description
        """
        pattern  = 'CM_\s+(?P<node>BU_)?(?P<msg>BO_)?(?P<sig>SG_)?\s*'
        pattern += '(?P<can_id>\d*)?\s*(?P<name>\S*)?\s*"(?P<value>.+)'
        reg = re.search(pattern, desc_str)

        desc_item = None
        if reg.group('node'):
            desc_item = self._canbus.nodes[reg.group('name').strip()]
        elif reg.group('msg'):
            desc_item = self._canbus.get_message(int(reg.group('can_id')))
        elif reg.group('sig'):
            desc_item = self._canbus.get_signal(can_id=int(reg.group('can_id')), name=reg.group('name').strip())
        else:
            desc_item = self._canbus

        value = reg.group('value')

        if value.strip()[-2:] == '";':
            desc_item.description = value.replace('";', '')
            self._mode = ('NORMAL', None)
        else:
            self._force_parser = self._parse_multiline_description
            self._mode = ('MULTILINE_DESCRIPTION', (desc_item, value + '\n'))

    def _parse_multiline_description(self, line):
        if line.strip()[-2:] == '";':
            self._mode[1][0].description = self._mode[1][1] + line.replace('";', '')
            self._force_parser = False
            self._mode = ('NORMAL', None)
        else:
            self._mode = (self._mode[0], (self._mode[1][0], self._mode[1][1] + line))

    def _parse_bus_configuration(self, bus_config_str):
        pattern = 'BS_\s*:\s*(?P<speed>\d+)?\s*'
        reg = re.search(pattern, bus_config_str)
        if reg.group('speed'):
            self._canbus.speed = int(reg.group('speed'))

    def _parse_attribute_definition(self, attribute_definition_str):
        pattern  = 'BA_DEF_\s+(?P<obj_type>...)?\s*"(?P<attr_name>\S+)"\s+'
        pattern += '(?P<attr_type>\S+)\s*(?P<attr_config>.+)?\s*;'
        reg = re.search(pattern, attribute_definition_str)

        obj_type = CANBus
        if 'BU_' in reg.groups():
            obj_type = CANNode
        elif 'BO_' in reg.groups():
            obj_type = CANMessage
        elif 'SG_' in reg.groups():
            obj_type = CANSignal

        ad = None
        if reg.group('attr_type') == 'FLOAT':
            reg_cfg = re.search('\s*(?P<min>\S+)\s*(?P<max>\S+)', reg.group('attr_config'))
            ad = CANFloatAttributeDefinition(reg.group('attr_name'), obj_type,
                                             float(reg_cfg.group('min')), float(reg_cfg.group('max')))
        elif reg.group('attr_type') == 'INT':
            reg_cfg = re.search('\s*(?P<min>\S+)\s*(?P<max>\S+)', reg.group('attr_config'))
            ad = CANIntAttributeDefinition(reg.group('attr_name'), obj_type,
                                             float(reg_cfg.group('min')), float(reg_cfg.group('max')))
        elif reg.group('attr_type') == 'STRING':
            ad = CANStringAttributeDefinition(reg.group('attr_name'), obj_type)
        elif reg.group('attr_type') == 'ENUM':
            values = reg.group('attr_config').split(',')
            values = list(map(lambda val: val.replace('"', '').strip(), values))
            ad = CANEnumAttributeDefinition(reg.group('attr_name'), obj_type, values)

        self._canbus.attribute_definitions.add_attribute_definition(ad)
