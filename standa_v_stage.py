#!/usr/bin/env python3
from argparse import ArgumentParser
from libscrc import modbus

from daemon import SimpleFactory, SimpleProtocol, SerialUSBProtocol, catch
from command import sanitize_command_line
import sys

# For debugging
import logging
logging.basicConfig(level=logging.ERROR)
import pdb

class DaemonProtocol(SimpleProtocol):
    """Protocol specifying how to communicate with the Standa 8MSC5-USB controller.
    """
    _debug = False  # Display all traffic for debug purposes.

    @catch
    def mbytes(self, cmd, pars, reserved_bytes=0):
        r"""Assemble the byte string of a four-character command as specified in
        the Standa 8MSC5-USB user manual.
        cmd : str
            name of the command
        pars : list [[int, str]]
            list of parameter values and the number of bytes they should take
        reserved_bytes : int, default: 0
            bytes to append TODO: clarify purpose
        """
        bss = cmd.encode('ascii')+b''.join([int(p[1]).to_bytes(p[0], 'little', signed=True) for p in pars])
        if reserved_bytes:
            bss += reserved_bytes*b'\xcc'
        bss += modbus(bss[4:]).to_bytes(2, 'little')
        return bss

    @catch
    def parsePars(self, cmd, pars_o, ss, rbs, nb=4):
        """Valiate and parse the parameters of a command, then send it.
        cmd : str
            command to be sent
        pars_o : list [[int, str]]
            specification of parameters as [number of bytes, name]
        ss : list [str]
            values of the parameters, in the same order as pars_o
        rbs : int
            reserved bytes appended by mbytes
        nb : int, default: 4
            number of bytes expected as a response, default of 4 is for commands
            like move, set_move_pars, move_in_direction.
        """
        # validate:
        # supply as many parameters `ss` as in specification `pars_o`
        if len(pars_o) != len(ss):
            return False
        # "labeled" means parameters of the form `key:value`
        # but the code also accepts keyfoo:value or fookey:value
        # so, e.g. parsing for position, if uposition is first, will not work
        # TODO: fix this
        # vs. unlabeled which are just `value`

        # either all need to be labeled, or none
        is_labeled = False
        if all(':' in sss for sss in ss):
            is_labeled = True
        elif not all(':' not in sss for sss in ss):
            return False
        # parse into `pars`:
        pars = []
        # variant one: long list comprehension relying on implicit assumptions
        for n in range(len(pars_o)):
            pars += [[pars_o[n][0], ss[n]]]
            if is_labeled:
                pars[-1][1] = [i for i in ss if pars_o[n][1] in i][0].split(':')[1]
        # variant two: safer using a dictionary
        '''
        par_dict = {}
        if is_labeled:
            par_dict = {_s.split(':')[0]:_s.split(':')[1] for _s in ss}
        else:
            par_dict = {_par_o[1]:_s for _par_o,_s in zip(pars_o, ss)}

        for _p in pars_o:
            # this would be cooler with a tuple
            _length = _p[0]
            _name = _p[1]
            pars += [[_length, par_dict[_name]]]
        # is this really better...?
        '''
        # variant three (to be implemented):
        # following key=value convention and using Sergey's Command class
        # to not duplicate code at all

        # assemble byte string
        mstr = self.mbytes(cmd, pars, reserved_bytes=rbs)

        # if it's not empty, send it.
        if mstr:
            obj['hw'].protocol.Imessage(mstr, nb=nb, source=self.name)
        return True

    @catch
    def processMessage(self, string):
        """Process the message `string` sent to ccdlab. This can be
            1) a command specified by the controller manual
            2) a higher-level command defined in this method
            (either meant for the device, or to be handled within ccdlab)
        A command will be parsed, processed into a version understood by the
        device, and appended to the command queue.
        """
        # TODO: use command.Command,
        # stop duplicating code,
        # replace the hard to navigate `if sstring=='command': ... break` structure
        # Solve how SimpleProtocol.processMessage already takes up some of the task.
        # TODO: document command syntax in docstring.
        cmd = SimpleProtocol.processMessage(self, string)
        if cmd is None:
            return

        for sstring in sanitize_command_line(string).split(';'):
            sstring = sstring.lower()
            while True:
                # managment commands
                if sstring == 'get_status':
                    self.message(
                        'status hw_connected={hw_connected} position={position} uposition={uposition} encposition={encposition} speed={speed} uspeed={uspeed} accel={accel} decel={decel} anti_play_speed={anti_play_speed} uanti_play_speed={uanti_play_speed}'.format(**self.object))
                    break
                if sstring == 'timeout':
                    self.factory.log('command timeout - removing command from list and flushing buffer')
                    obj['hw'].protocol._buffer = b''  # empty buffer after timeout
                    if obj['hw'].protocol.commands:
                        obj['hw'].protocol.commands.pop(0)
                    break
                if not obj['hw_connected']:
                    break

                Imessage = obj['hw'].protocol.Imessage
                #if string == 'sync': # TODO verify, does `sync` need to be alone?
                if sstring == 'sync':
                    # sync after failed command
                    Imessage(bytes(64), nb=64, source=self.name) # the current `twisted` version expects the immutable `bytes`, not the mutable `bytearray`
                    break

                # general query command (xxxx commands from manual)(for specifically implemented commands see below)
                ss = sstring.split('<')
                if len(ss) == 2 and len(ss[1]) == 4:
                    Imessage(ss[1], nb=int(ss[0]), source=self.name)
                    daemon.log('command ', sstring)
                    break
                elif len(ss) > 1:
                    daemon.log('unable to parse command, format should be "nb<xxxx" insted of: '+sstring, 'error')
                    break

                # human-readable versions of the most common controller commands:

                if sstring == 'get_device_info':
                    # get some device info (model, etc.)
                    Imessage('gsti', nb=70, source=self.name)
                    break

                if sstring == 'get_move_pars':
                    # get movement parameters
                    Imessage('gmov', nb=30, source=self.name)
                    break

                if sstring == 'get_position':
                    # get movement parameters
                    Imessage('gpos', nb=26, source=self.name)
                    break

                if sstring.startswith('set_move_pars'):
                    # set movement parameters, examples:
                    # set_move_pars speed:2000 uspeed:0 accel:2000 decel:5000 anti_play_speed:2000 uanti_play_speed:0
                    # set_move_pars 2000 0 2000 5000 2000 0
                    pars_o = [[4, 'speed'], [1, 'uspeed'], [2, 'accel'], [2, 'decel'], [4, 'anti_play_speed'], [1, 'uanti_play_speed']]
                    if self.parsePars('smov', pars_o, sstring.split(' ')[1:], 10):
                        daemon.log('Setting movement parameters to ', sstring)
                        break

                if sstring.startswith('move_in_direction'):
                    # set movement parameters
                    pars_o = [[4, 'dpos'], [2, 'udpos']]
                    if self.parsePars('movr', pars_o, sstring.split(' ')[1:], 6):
                        daemon.log('move ', sstring)
                        break

                if sstring.startswith('move'):
                    # set movement parameters
                    pars_o = [[4, 'pos'], [2, 'upos']]
                    if self.parsePars('move', pars_o, sstring.split(' ')[1:], 6):
                        daemon.log('move ', sstring)
                        break

                if sstring == 'set_zero':
                    # set current position as zero
                    Imessage('zero', nb=4, source=self.name)
                    daemon.log('reset zero')
                    break

                # general set command (xxxx commands from manual) (for specifically implemented commands see below)
                # command example: smov 4:2000 1:0 2:2000 2:5000 4:2000 1:0 10:r
                # for these commands one needs to specity the number of bytes given value occupies:
                # nbytes1:value1 nbytes2:value2 nreserved:r
                # TODO: change to meaningful variable names
                ss = sstring.split(' ')
                if all(':' in sss for sss in ss[1:]) and all(nnn.split(':')[0].isdigit() for nnn in ss[1:]):
                    cmd = ss[0]
                    ss = ss[1:]
                    rbs = 0
                    if len(ss) > 1 and ss[-1].split(':')[1] == 'r':
                        rbs = int(ss[-1].split(':')[0])
                        ss = ss[:-1]
                    pars = [sss.split(':') for sss in ss]
                    pars = list(map(lambda x: [int(x[0]), x[1]], pars))
                    mstr = self.mbytes(cmd, pars, rbs)
                    if mstr:
                        Imessage(mstr, nb=4, source=self.name)
                        daemon.log('command ', sstring)
                    break
                print('command', sstring, 'not implemented!')
                break


class StandaVSProtocol(SerialUSBProtocol):
    """Hardware protocol.
    """
    # TODO: explain.

    _bs = b''

    @catch
    def __init__(self, serial_num, obj,
                 refresh=1.0,
                 debug=False,
                 ):
        self.commands = []  # Queue of command sent to the device which will provide replies, each entry is a dict with keys "cmd","source"
        self.status_commands = [[26, 'gpos'], [30, 'gmov']]  # commands send when device not busy to keep tabs on the state
        if debug:
            self.status_commands = [] # TODO: make a separate option?

        super().__init__(obj=obj, serial_num=serial_num, refresh=refresh, debug=debug,
                        # 8SMC5-USB programming manual, sec. 6.2.1
                         baudrate=115200,
                         bytesize=8,
                         parity='N',
                         stopbits=2,
                         timeout=400,
                         )

    @catch
    def connectionMade(self):
        self.commands = []
        super().connectionMade()
        self.object['hw_connected'] = 1

    @catch
    def connectionLost(self, reason):
        super().connectionLost(reason)
        self.object['hw_connected'] = 0
        self.commands = []
        self.object.update({
            'position' : 'nan',
            'uposition' : 'nan',
            'encposition' : 'nan',
            'speed' : 'nan',
            'uspeed' : 'nan',
            'accel' : 'nan',
            'decel' : 'nan',
            'anti_play_speed' : 'nan',
            'uanti_play_speed' : 'nan',
        })

    @catch
    def processMessage(self, string):
        # Process the device reply
        if self._debug:
            print("hw cc > %s" % string)
        self.commands.pop(0)

    @catch
    def iscom(self, com):
        if self.commands[0]['cmd'] == com and self._bs[:4].decode('ascii') == com:
            self._bs = self._bs[4:]
            return True
        return False

    @catch
    def sintb(self, nb):
        ss = self._bs[:nb]
        self._bs = self._bs[nb:]
        return str(int.from_bytes(ss, "little"))

    @catch
    def strb(self, nb):
        ss = self._bs[:nb]
        self._bs = self._bs[nb:]
        return (ss.strip(b'\x00')).decode('ascii')

    @catch
    def processBinary(self, bstring):
        # Process the device reply
        self._bs = bstring
        if self._debug:
            print("hw bb > %s" % self._bs)
        if len(self.commands):
            if self._debug:
                print("last command which expects reply was:", self.commands[0]['cmd'])
                print("received reply:", self._bs)
            if (b'errc' or b'errd' or b'errv') in self._bs:
                print('command', self.commands[0]['cmd'], 'produced error', self._bs)
                self._buffer = b''  # empty buffer after error

            r_str = None
            while True:

                # check buffer empty and checksum
                if self._buffer != b'':
                    print('warning buffer not empty after expected number of bytes')
                    self._buffer = b''  # empty buffer
                if len(self._bs) > 4 and self.commands[0]['status'] == 'sent' and modbus(self._bs[4:]) != 0:
                    r_str = 'checksum failed'
                    self._buffer = b''
                    break

                if self.commands[0]['status'] == 'sync':
                    # sync after failed command
                    r_str = 'sync'
                    if len(self.commands) > 1 and self.commands[1]['status'] == 'sent':
                        # remove failed command
                        self.commands.pop(0)
                    break

                r_str = b'' # response string
                if self.iscom('gsti'):
                    r_str = self.strb(16)+' '
                    r_str += self.strb(24)
                    break

                if self.iscom('gmov'):
                    self.object.update({
                        'speed' : self.sintb(4),
                        'uspeed' : self.sintb(1),
                        'accel' : self.sintb(2),
                        'decel' : self.sintb(2),
                        'anti_play_speed' : self.sintb(4),
                        'uanti_play_speed' : self.sintb(1),
                    })
                    # TODO: use command.Command
                    if self.commands[0]['status'] != 'sent_status':
                        r_str = 'speed:{speed} uspeed:{uspeed} accel:{accel} anti_play_speed:{anti_play_speed} uanti_play_speed:{uanti_play_speed}'.format(**(self.object))
                    break

                if self.iscom('gpos'):
                    self.object.update({
                        'position' : self.sintb(4),
                        'uposition' : int(self.sintb(2)),
                        'encposition' : int(self.sintb(8)),
                    })
                    if self.commands[0]['status'] != 'sent_status':
                        # TODO: use command.Command
                        r_str = 'position:{position} uposition:{uposition} encposition:{encposition}'.format(**(self.object))
                    break

                # not recognized command, just pass the output
                r_str = self._bs
                break
            if type(r_str) == str:
                daemon.messageAll(r_str, name=self.commands[0]['source'])
            elif r_str != b'':
                daemon.messageAll(r_str, name=self.commands[0]['source'])
        self.commands.pop(0)

    @catch
    def Imessage(self, string, nb, source='itself'):
        """Send outgoing message.
        string : bytes
            Message to be sent.
        nb : int
            number of bytes to expect in response
        """
        if self._debug:
            print(">> serial >>", string, 'expecting', nb, 'bytes')

        if string[0] == 0:
            # sync after failed command, the sync is put at the front of the queue
            self.commands = [{'cmd': string, 'nb': nb, 'source': source, 'status': 'sync'}]+self.commands
        else:
            self.commands.append({'cmd': string, 'nb': nb, 'source': source, 'status': 'new'})

    @catch
    def update(self):
        if self._debug:
            print("----------------------- command queue ----------------------------")
            for k in self.commands:
                print(k['cmd'], k['nb'], k['source'], k['status'])
            print("===================== command queue end ==========================")

        if len(self.commands) and obj['hw_connected']:
            if self.commands[0]['status'].startswith('sent'):
                return
            if self.commands[0]['status'] == 'new':
                self.commands[0]['status'] = 'sent'
            elif self.commands[0]['status'] == 'status':
                self.commands[0]['status'] = 'sent_status'
            self._binary_length = max(4, self.commands[0]['nb'])
            self.message(self.commands[0]['cmd'])
        else:
            for k in self.status_commands:
                self.commands.append({'cmd': k[1], 'nb': k[0], 'source': 'itself', 'status': 'status'})


if __name__ == '__main__':
    parser = ArgumentParser(description='Module for the Standa vertical stage 8MVT100-25-1.')
    parser.add_argument('-s', '--serial-num',
                        help='Serial number of the device to connect to, used in SerialUSBProtocol.__init__. \n Generally written on the bottom of the controller, here the number is in hexadecimal and zero-padded to 8 digits.',
                        action='store', type=str, default='00004CCA') # written on controller, in hexadecimal, zero-padded to 8
    parser.add_argument('-p', '--port',
                        help='Daemon port, where `telnet localhost [PORT]` sends commands to the daemon.',
                        action='store', type=int, default=7027)
    parser.add_argument('-n', '--name',
                        help='Daemon name',
                        action='store', type=str, default='standa_v_stage')
    parser.add_argument('-r', '--refresh',
                        help='Interval in seconds to update the command queue. For [REFRESH]<=0, use the default defined in SerialUSBProtocol.',
                        action='store', type=float, default=1.0)
    parser.add_argument('-D', '--debug',
                        help='Debug mode',
                        action="store_true")


    (options, args) = parser.parse_known_args()

    # Object holding actual state and work logic.
    # May be anything that will be passed by reference - list, dict, object etc
    obj = {'hw_connected': 0,
           'position': 'nan', 'uposition': 'nan', 'encposition': 'nan',
           'speed': 'nan', 'uspeed': 'nan', 'accel': 'nan', 'decel': 'nan', 'anti_play_speed': 'nan', 'uanti_play_speed': 'nan', }

    daemon = SimpleFactory(DaemonProtocol, obj)
    daemon.name = options.name
    obj['daemon'] = daemon

    proto = StandaVSProtocol(serial_num=options.serial_num,
                             obj=obj,
                             refresh=options.refresh,
                             debug=options.debug,
                             )

    if options.debug:
        daemon._protocol._debug = True

    # Incoming connections
    daemon.listen(options.port)

    #
    daemon._reactor.run()
