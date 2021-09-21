#!/usr/bin/env python3

import os
import sys
import time

from daemon import SimpleFactory, SimpleProtocol, catch
from command import Command


class DaemonProtocol(SimpleProtocol):
    _debug = False  # Display all traffic for debug purposes

    @catch
    def __init__(self):
        SimpleProtocol.__init__(self)
        self.addr = -1

    @catch
    def processMessage(self, string):
        # It will handle some generic messages and return pre-parsed Command object
        cmd = SimpleProtocol.processMessage(self, string)
        if cmd is None:
            return
        
        obj = self.object  # Object holding the state
        daemon = self.factory
        hw = obj['hw']  # HW factory

        if cmd.name == 'get_status':
            self.message('status hw_connected=%s current_addr=%d' % (obj['hw_connected'], obj['current_addr']))
        elif cmd.name == 'set_addr':
            self.addr = int(cmd.args[0]) if len(cmd.args) else -1
        elif cmd.name == 'send':
            # TODO: escape all necessary characters in the string
            self.sendCommand(" ".join(cmd.chunks[1:]))
        elif self.addr >= 0:
            # FIXME: we should somehow prevent the commands already processed in superclass from arriving here
            self.sendCommand(string)

    @catch
    def sendCommand(self, string):
        obj = self.object  # Object holding the state
        hw = obj['hw']  # HW factory

        # for GPIB connections send the address as the source ID
        if self.addr > 0:
            cmd = string.split('$')
            if len(cmd) == 1:
                hw.messageAll(cmd[-1], type='hw', source=self.addr)
            elif len(cmd) == 2:
                hw.messageAll(cmd[-1], type='hw', source=self.addr, keep=(cmd[0] == '?'))
            else:
                raise ValueError('cannot parse command')
        else:
            hw.messageAll(string, type='hw', source=self.name)


class GPIBProtocol(SimpleProtocol):
    _debug = False  # Display all traffic for debug purposes
    _tcp_keepidle = 10  # Faster detection of peer disconnection
    _refresh = 0.01

    def __init__(self):
        SimpleProtocol.__init__(self)
        self.command_id = 0
        self.commands = []
        self.name = 'hw'
        self.type = 'hw'
        self.next_addr = -1
        self.daemonQs = {}  # queues for GPIB devices commands, the key as the address
        self.gpibAddrList = []  # a list of the active GPIB connection addresses
        self.readBusy = [False,time.time()]

    @catch
    def connectionMade(self):
        SimpleProtocol.connectionMade(self)
        self.object['hw_connected'] = 1
        # We are resetting it to be sure that we have proper address later
        self.object['current_addr'] = -1
        self.message('++auto 0')

    @catch
    def connectionLost(self, reason):
        self.object['hw_connected'] = 0
        SimpleProtocol.connectionLost(self, reason)

    @catch
    def processMessage(self, string):
        if self._debug:
            print ("GPIB >> ", repr(string))

        SimpleProtocol.processMessage(self, string)
        daemon = self.object['daemon']

        # Process the device reply
        if len(self.commands) and self.commands[0] in ['++addr']:
            # We are silently ignoring the results from these commands
            self.commands.pop(0)  # Remove the command from queue
        else:
            for conn in daemon.connections:
                if conn.addr == self.object['current_addr'] and self.object['current_addr'] >= 0:
                    # Send the device reply to the client connection with given address
                    conn.message(string)
        self.readBusy = [False,time.time()]

    @catch
    def message(self, string, keep=False, source='self'):
        """
        Send the message to the controller. If keep=True, append the command name to
        internal queue so that we may properly recognize the reply
        """
        self.update_daemonQs()

        if source in self.daemonQs.keys():
            # This is a GPIB connection, add the command to the corresponding queue
            self.daemonQs[source].append({'cmd': string})

            if keep and source != 'itself' and string not in ['++srq']:
                self.daemonQs[source].append({'cmd': '++read eoi'})
        else:
            # Handle non-GPIB messages as usual
            SimpleProtocol.message(self, '%s' % (string))
            if keep:
                self.commands.append(string)

    @catch
    def update_daemonQs(self):
        """
        Check if there are any new GPIB connections.
        If a new GPIB device connected, add it, disconnected devices should stay in the dict
        """
        self.gpibAddrList = [
            _.addr for _ in self.object['daemon'].connections if _.addr > 0]
        for addr in self.gpibAddrList:
            if addr not in self.daemonQs.keys():
                self.daemonQs[addr] = []

    @catch
    def update(self):
        if self._debug:
            print ("Busy flag", self.readBusy)
        if self.readBusy[0]:
            if time.time()-self.readBusy[1]>3:
                SimpleProtocol.message(self, '++read eoi') 
                self.readBusy[1]=time.time()
            return
        self.update_daemonQs()
        if self._debug:
            print ("daemonQs:", self.daemonQs)
        
        last_addr=self.next_addr
        no_commands = True
        for k in range(len(self.gpibAddrList)):
            try:
                d=self.gpibAddrList.index(last_addr) - k - 1
                self.next_addr = self.gpibAddrList[d]
                if self._debug:
                    print ("Found last addr (", self.next_addr, ")")
            except BaseException:
                self.next_addr = self.gpibAddrList[0]
                if self._debug:
                    print ("Last addr not found (perhaps disconnected meanwhile), switching to the first (", self.next_addr, ")")
            if len(self.daemonQs[self.next_addr]):
                if self.object['current_addr'] != self.next_addr:
                    if self._debug:
                        print ("switching to addr (", self.next_addr, ")")
                    SimpleProtocol.message(self, '++addr %i' % self.next_addr)
                    self.object['current_addr'] = self.next_addr
                    #time.sleep(0.1) # we need to wait a bit here to allow the controller to finish changing the addr
                cmd = self.daemonQs[self.next_addr].pop(0)
                no_commands = False
                if cmd['cmd'] in ['++read eoi', '++addr', '++srq']:
                    self.readBusy = [True,time.time()]
                SimpleProtocol.message(self, cmd['cmd'])  
                break
        if no_commands and (time.time()-self.readBusy[1])>1.:
            if self._debug:
                print ("There is either no GPIB connections, or nothing to do for them, doing ++addr to keep the connection alive")
            self.message('++addr', keep=True)
            self.readBusy = [True,time.time()]


if __name__ == '__main__':
    from optparse import OptionParser

    parser = OptionParser(usage="usage: %prog [options] arg")
    parser.add_option('-H', '--hw-host', help='Hardware host to connect', action='store', dest='hw_host', default='192.168.1.3')
    parser.add_option('-P', '--hw-port', help='Hardware port to connect', action='store', dest='hw_port', type='int', default=1234)
    parser.add_option('-p', '--port', help='Daemon port', action='store', dest='port', type='int', default=7020)
    parser.add_option('-n', '--name', help='Daemon name', action='store', dest='name', default='gpib')
    parser.add_option("-D", '--debug', help='Debug mode', action="store_true", dest="debug")

    (options, args) = parser.parse_args()

    # Object holding actual state and work logic.
    # May be anything that will be passed by reference - list, dict, object etc
    obj = {'hw_connected': 0, 'current_addr': -1}

    # Factories for daemon and hardware connections
    # We need two different factories as the protocols are different
    daemon = SimpleFactory(DaemonProtocol, obj)
    hw = SimpleFactory(GPIBProtocol, obj)

    if options.debug:
        daemon._protocol._debug = True
        hw._protocol._debug = True

    daemon.name = options.name

    obj['daemon'] = daemon
    obj['hw'] = hw

    # Incoming connections
    daemon.listen(options.port)
    # Outgoing connection
    hw.connect(options.hw_host, options.hw_port)

    daemon._reactor.run()
