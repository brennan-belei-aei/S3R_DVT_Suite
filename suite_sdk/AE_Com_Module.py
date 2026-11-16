"""
Script Name:            AE_Com_Module.py
Date Created:           Mar 13 2014
Created by:             Dave Roberg
Date last modified:     Dec 19 2017
Last Modified by:       Dave Roberg
Revision of Python:     3.3.3.3
Brief Description:      Connect to serial, TCP, UDP, AEGateway, ACOnOffFixture,
                        or ASCII port.
Dependances:            ctypes, socket, serial, and time (standard libraries)
"""

import ctypes
import socket
import serial
import time

AEBUS_DEFAULT_BAUD = 19200
AEBUS_FAST_BAUD = 115200


class __AEBUSError(Exception):
    """Takes in a csr and looks up what the error is and returns the error"""
    faults={-2:'COM is not available',
            -3:'aebus timeout',
            -4:'checksum',
            -5:'COM is busy',
            -6:'bad ack',
            -7:'wrong port',
            -8:'read error',
            -9:'no ack'}

    def __init__(self, ret_val):
        """Create a class for AEBUS errors
        Reads in an error code"""
        if ret_val > 2**15: 
            ret_val = ret_val - 2**16
        self.err_code = ret_val

    def __str__(self):
        """Creates the error message from AEBUS"""
        if self.err_code in self.faults: 
            msg = self.faults[self.err_code]
        else: 
            msg = 'Unknown error, Error ID = %d' %self.err_code
        return "unable to communicate with device : " + msg
        
        
class AEB32(object):
    _err_code = {'OK':0,
                 'WRITE_ERROR':-1,
                 'READ_ERROR':-2,
                 'TIMEOUT':-3,
                 'CKSUM':-4,
                 'NAK':-5,
                 'BAD_ACK':-6,
                 'READ_ACK':-7,
                 'WRITE_ACK':-8,
                 'READ_DATA':-9,
                 'INVALID_HANDLE_VALUE':-10}
    """Create a serial communication object
    Input:
        comport - str Comport formatted string e.g. COM1
        baud - int The baud rate of the fixture/generator
            Default: 19200
        timeout - int AE comport timeout value
            Default: 1000
        ae_addr - AECom address number
            Default: 1"""
    def __init__(self, comport, baud=AEBUS_DEFAULT_BAUD, timeout=1000, ae_addr=1):
        """Create a serial communication object
        Input:
            comport - str Comport formatted string e.g. COM1
            baud - int The baud rate of the fixture/generator
                Default: 19200
            timeout - int AE comport timeout value
                Default: 1000
            ae_addr - AECom address number
                Default: 1"""
        # This is for consistency sake, Serial only needs the number, not the
        # string
        # com_num = int(comport[3:]) - 1   # Serial is zero based
        # Timeout is in secs, not millisecs
        self.device = serial.Serial(comport, baud, timeout=timeout / 1000)
        self.device.bytesize = serial.EIGHTBITS
        self.device.parity = serial.PARITY_ODD
        self.ae_addr = ae_addr
        self._max_data = 259
        self._ACK = 0x06
        self._NAK = 0x15
        
    def AEBWR(self, cmd, send_sz, data):
        """Write an AEBus serial command.
        Input:
            cmd - int Command number
            send_sz - int Number of bytes to send
            data - bytearray Data to send
        Output:
            int Error code
            int Bytes received
            bytes Data received"""
        buf = [0] * 3
        buf[0] = self.ae_addr << 3
        buf[1] = cmd
        read_len = 3
        
        # Set up header
        if send_sz < 7:
            buf[0] |= send_sz
            n_data = 2
        else:
            buf[0] |= 7
            buf[2] = send_sz
            n_data = 3
        if send_sz and n_data == 2:
            del buf[-1]
            buf.extend(data[:send_sz])
            buf.append(0)
            n_data += send_sz
        elif send_sz:
            buf.extend(data[:send_sz])
            buf.append(0)
            n_data += send_sz
            
        # Calculate checksum
        for checksum in range(n_data):
            buf[n_data] ^= buf[checksum]
        n_data += 1
        
        # Write packet to generator
        if self.device.write(buf) != len(buf):
            return self._err_code['WRITE_ERROR'], 0, [0]
        if self.ae_addr == 0:
            return self._err_code['OK'], 0, [0]
        # Get ACK from generator
        ack = self.device.read(1)
        if len(ack) == 0:
            return self._err_code['READ_ACK'], 0, [0]
        # Verify ACK was returned
        if len(ack) != 1:
            return self._err_code['TIMEOUT'], 0, [0]
        if ack[0] == self._NAK:
            return self._err_code['NAK'], 0, [0]
        if ack[0] != self._ACK:
            return self._err_code['BAD_ACK'], 0, [0]
        # Retrieve data if ACK is good
        header = self.device.read(3)
        if len(header) == 0:
            return self._err_code['READ_DATA'], 0, [0]
        read_len = header[0] & 7
        temp = -1
        if read_len == 7:
            read_len = header[2] + 1
            checksum = header[0] ^ header[1] ^ header[2]
        else:
            temp = header[2]
            checksum = header[0] ^ header[1]
        
        # Read checksum
        if read_len != 0:
            data = self.device.read(read_len)
            if len(data) == 0:
                return self._err_code['READ_DATA'], 0, [0]
            if temp != -1:
                data = bytes([temp]) + data
                read_len += 1
            for check in range(read_len):
                checksum ^= data[check]
        # Check checksum
        if checksum:
            return self._err_code['CKSUM'], 0, [0]
            
        # Send ACK
        if self.device.write([self._ACK]) != 1:
            return self._err_code['WRITE_ACK'], 0, [0]
        return self._err_code['OK'], read_len - 1, data[:read_len - 1]
        
    def NOACK(self, cmd, send_sz, data, delay):
        """Write an AEBus serial command.
        Input:
            cmd - int Command number
            send_sz - int Number of bytes to send
            data - bytearray Data to send
            delay - int ACK delay in ms, send in 0 for no ack
        Output:
            int Error code
            int Bytes received
            bytes Data received"""
        buf = [0] * 3
        buf[0] = self.ae_addr << 3
        buf[1] = cmd
        read_len = 3
        
        # Set up header
        if send_sz < 7:
            buf[0] |= send_sz
            n_data = 2
        else:
            buf[0] |= 7
            buf[2] = send_sz
            n_data = 3
        if send_sz and n_data == 2:
            del buf[-1]
            buf.extend(data[:send_sz])
            buf.append(0)
            n_data += send_sz
        elif send_sz:
            buf.extend(data[:send_sz])
            buf.append(0)
            n_data += send_sz
            
        # Calculate checksum
        for checksum in range(n_data):
            buf[n_data] ^= buf[checksum]
        n_data += 1
        
        # Write packet to generator
        if self.device.write(buf) != len(buf):
            return self._err_code['WRITE_ERROR'], 0, [0]
        if self.ae_addr == 0:
            return self._err_code['OK'], 0, [0]
        # Get ACK from generator
        ack = self.device.read(1)
        if len(ack) == 0:
            return self._err_code['READ_ACK'], 0, [0]
        # Verify ACK was returned
        if len(ack) != 1:
            return self._err_code['TIMEOUT'], 0, [0]
        if ack[0] == self._NAK:
            return self._err_code['NAK'], 0, [0]
        if ack[0] != self._ACK:
            return self._err_code['BAD_ACK'], 0, [0]
        # Retrieve data if ACK is good
        header = self.device.read(3)
        if len(header) == 0:
            return self._err_code['READ_DATA'], 0, [0]
        read_len = header[0] & 7
        temp = -1
        if read_len == 7:
            read_len = header[2] + 1
            checksum = header[0] ^ header[1] ^ header[2]
        else:
            temp = header[2]
            checksum = header[0] ^ header[1]
        
        # Read checksum
        if read_len != 0:
            data = self.device.read(read_len)
            if len(data) == 0:
                return self._err_code['READ_DATA'], 0, [0]
            if temp != -1:
                data = bytes([temp]) + data
                read_len += 1
            for check in range(read_len):
                checksum ^= data[check]
        # Check checksum
        if checksum:
            return self._err_code['CKSUM'], 0, [0]
            
        # Send ACK
        if delay != 0:
            time.sleep(delay / 1000)
            if self.device.write([self._ACK]) != 1:
                return self._err_code['WRITE_ACK'], 0, [0]
        return self._err_code['OK'], read_len - 1, data[:read_len - 1]
            	
    def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args, delay=None):
        """Create a send buffer and return formatted receive buffer  
        Input:
            cmd - int AEBus command number
            send_sizes - str Format string to send (see create_buffer_cmd Documentation.doc
                         for more details)
            recv_sizes - str Format string to receive (see create_buffer_cmd Documentation.doc
                         for more details)
            *args - Dynamic arugments of any type to send
            delay - int Optional, delay time for ack, enter 0 for no ack
        Output:
            vals - list List containing formatted output"""
        vals = []
        # Create a buffer for sending to AEBus
        buf, send_sz = create_buffer(send_sizes, args)
        if delay == None:
            ret, bytes_read, buf = self.AEBWR(cmd, send_sz, buf)
        else:
            ret, bytes_read, buf = self.NOACK(cmd, send_sz, buf, delay)
        # Add the ret code and bytes read from AEBus to return list
        vals.append(ret)
        # For a case where user wants a string returned, but a csr was returned
        # Some values for a csr would crash the program because of Unicode 
        # encoding doesn't support that value
        if ':s' in recv_sizes and bytes_read == 1:
            vals.append(0)
            vals.append(buf[0])
        # For all commands, if recv_sizes are 0:0 then a csr will be returned
        # if one was returned
        elif recv_sizes == '0:0':
            if bytes_read == 1 and buf[0] != 0:
                vals.append(0)
            else:
                vals.append(bytes_read)
            vals.append(buf[0])
        # For set commands, if csr is not 0, then a csr is returned
        elif cmd < 128 and buf[0] != 0:
            vals.append(0)
            vals.append(buf[0])
        # For report commands that aren't '1:1', if 1 byte was received, then 
        # a csr was returned
        elif cmd >= 128 and recv_sizes != '1:1' and bytes_read == 1:
            vals.append(0)
            vals.append(buf[0])
        # For report commands that are '1:1', if nothing was returned, then 
        # there was an error
        elif cmd >= 128 and recv_sizes == '1:1' and bytes_read == 0:
            vals.append(0)
            vals.append('Error: No data received')
        else:
            vals.append(bytes_read)
            # Create a list of specified data types to return
            vals = create_return(vals, recv_sizes, bytes_read - 1, buf)
        return vals
                        
    def close(self):
        """close the comport"""
        self.device.close()
        
        
class AETCP(object):
    """Create a TCP socket
    Input:
        unit_address - int AE address number (currently unknown if it is 
        necessary)
            Default: 1"""
    def __init__(self, unit_address=1):
        """Create a TCP socket
        Input:
            unit_address - int AE address number (currently unknown if it is 
            necessary)
                Default: 1"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addr = unit_address
        
    def connect(self, ip_address, port=502):
        """Connect to an IP Address and port
        Input:
            ip_address - str The IP address of fixture/generator
            port - int TCP port number
                Default: 502"""
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.ip_address = ip_address
        self.port = port
        self.sock.connect((ip_address, port))
        
    # def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args):
    #     """Create a send buffer and return formatted receive buffer
    #     Input:
    #         cmd - int AEBus command number
    #         send_sizes - str Format string to send (see create_buffer_cmd Documentation.doc
    #                      for more details)
    #         recv_sizes - str Format string to receive (see create_buffer_cmd Documentation.doc
    #                      for more details)
    #         *args - Dynamic arugments of any type to send
    #     Output:
    #         vals - list List containing formatted output"""
    #     start = time.time()
    #     while time.time() - start < 5:
    #         buf, length = create_buffer(send_sizes, args)
    #         vals = []
    #         modbus_bytes = length + 6
    #         # modbus arguments, first 4 bytes are 0s, 5th argument is modbus
    #         # bytes / 256, 6th argument is modbus bytes % 256, 7th argument is
    #         # address, 8th argument is fc version, 9th agrument is cmd, 10th
    #         # argument is csr, 11th agrument is the length, and last
    #         # argument is 0.
    #         out_buf = (ctypes.c_ubyte * (12 + length))(0, 0, 0, 0,
    #                    int(modbus_bytes) // 256, int(modbus_bytes) % 256,
    #                    int(self.addr), 100, cmd, 0, int(length), 0)
    #
    #         for x in range(12, 12 + length):
    #            out_buf[x] = buf[x - 12]
    #
    #         # send the packet
    #         self.sock.sendall(out_buf)
    #
    #         # read response
    #         try:
    #
    #             def recv_all(sock, num_bytes):
    #                 data = bytearray()
    #                 while len(data) < num_bytes:
    #                     packet = sock.recv(num_bytes - len(data))
    #                     if not packet:
    #                         raise ConnectionError("Socket connection lost")
    #                     data.extend(packet)
    #                 print(int(data))
    #                 return data
    #
    #
    #             if args[0] != 57:
    #                 in_buf = self.sock.recv(270)
    #             else:
    #                 in_buf = self.sock.recv(65535)
    #                 #in_buf = recv_all(self.sock, 680)  # or whatever number of bytes you expect
    #
    #             # Append AEBus Error Code
    #             vals.append(int(in_buf[7]) & 0x80)
    #             # Append bytes received
    #             vals.append(len(in_buf) - 12)
    #             if recv_sizes == '1:1' and len(in_buf) == 12:
    #                 # Append the csr
    #                 vals.append(in_buf[9])
    #             elif recv_sizes == '0:0':
    #                 if in_buf[9] != 0:
    #                     vals.append(in_buf[9])
    #                 else:
    #                     vals.append(in_buf[12])
    #             else:
    #                 # If there is a csr insert the csr to the buffer
    #                 if in_buf[9] != 0:
    #                     vals.append(in_buf[9])
    #                 else:
    #                     if args[0] != 57:
    #                         vals = create_return(vals, recv_sizes, len(in_buf) - 13, in_buf[12:])
    #                     else:
    #                         vals = create_return(vals, recv_sizes, len(in_buf[12:]), in_buf[12:])
    #                         print(f'returned values are: {vals}')
    #             return vals
    #         except Exception as e:
    #             _e = e
    #             # try reconnecting if a socket crashed, i.e., generator was shut
    #             # down or resetted
    #             self.sock.close()
    #             self.__init__(self.addr)
    #             self.connect(self.ip_address, self.port)
    #     #If number of command attempts exceeds 100
    #     raise Exception("Ethernet Command Error:", _e)
    #

    def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args):
        """Create a send buffer and return formatted receive buffer
        Input:
            cmd - int AEBus command number
            send_sizes - str Format string to send (see create_buffer_cmd Documentation.doc
                         for more details)
            recv_sizes - str Format string to receive (see create_buffer_cmd Documentation.doc
                         for more details)
            *args - Dynamic arugments of any type to send
        Output:
            vals - list List containing formatted output"""
        start = time.time()
        while time.time() - start < 5:
            buf, length = create_buffer(send_sizes, args)
            vals = []
            modbus_bytes = length + 6
            # modbus arguments, first 4 bytes are 0s, 5th argument is modbus
            # bytes / 256, 6th argument is modbus bytes % 256, 7th argument is
            # address, 8th argument is fc version, 9th agrument is cmd, 10th
            # argument is csr, 11th agrument is the length, and last
            # argument is 0.
            out_buf = (ctypes.c_ubyte * (12 + length))(0, 0, 0, 0,
                                                       int(modbus_bytes) // 256, int(modbus_bytes) % 256,
                                                       int(self.addr), 100, cmd, 0, int(length), 0)

            for x in range(12, 12 + length):
                out_buf[x] = buf[x - 12]

            # send the packet
            self.sock.sendall(out_buf)

            # read response
            try:
                in_buf = self.sock.recv(270)
                # Append AEBus Error Code
                vals.append(int(in_buf[7]) & 0x80)
                # Append bytes received
                vals.append(len(in_buf) - 12)
                if recv_sizes == '1:1' and len(in_buf) == 12:
                    # Append the csr
                    vals.append(in_buf[9])
                elif recv_sizes == '0:0':
                    if in_buf[9] != 0:
                        vals.append(in_buf[9])
                    else:
                        vals.append(in_buf[12])
                else:
                    # If there is a csr insert the csr to the buffer
                    if in_buf[9] != 0:
                        vals.append(in_buf[9])
                    else:
                        vals = create_return(vals, recv_sizes, len(in_buf) - 13, in_buf[12:])
                return vals
            except Exception as e:
                _e = e
                # try reconnecting if a socket crashed, i.e., generator was shut
                # down or resetted
                self.sock.close()
                self.__init__(self.addr)
                self.connect(self.ip_address, self.port)
        # If number of command attempts exceeds 100
        raise Exception("Ethernet Command Error:", _e)

    def close(self):
        """Close the socket"""
        self.sock.close()
        
        
#class AEFC4_16(object):
#    """Create a TCP socket
#    Input:
#        unit_address - int AE address number (currently unknown if it is 
#        necessary)
#            Default: 1"""
#    def __init__(self, unit_address=1):
#        """Create a TCP socket
#        Input:
#            unit_address - int AE address number (currently unknown if it is 
#            necessary)
#                Default: 1"""
#        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#        self.addr = unit_address
#        
#    def connect(self, ip_address, port=502):
#        """Connect to an IP Address and port
#        Input:
#            ip_address - str The IP address of fixture/generator
#            port - int TCP port number
#                Default: 502"""
#        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
#        self.ip_address = ip_address
#        self.port = port
#        self.sock.connect((ip_address, port))
#        
#    
#        
#    def close(self):
#        """Close the socket"""
#        self.sock.close()
        
        
class AEGateway(object):
    """Connect to AEBus Gateway
    Input:
        ip_address - string IP address to connect to AEBus Gateway
                     Default: '127.0.0.1'
        port - int TCP port to connect to AEBus Gateway
               Default: 502
    Note:
        AEBus Gateway must be running for this to work"""
    def __init__(self, ip_address='127.0.0.1', port=502):
        """Connect to AEBus Gateway
        Input:
            ip_address - string IP address to connect to AEBus Gateway
                         Default: '127.0.0.1'
            port - int TCP port to connect to AEBus Gateway
                   Default: 502
        Note:
            AEBus Gateway must be running for this to work"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.ip_address = ip_address
        self.port = port
        self.sock.connect((self.ip_address, self.port))
        
    def available_com_list(self):
        """Prints out the available comports"""
        buf = self.create_buffer_cmd(0, '1:1', 's:s', 2)
        print(buf[2])
        
    def __fc23_packet(self, cmd, length):
        """Private method to create a fc23 header
        Input:
            cmd - int AEBus command number
            length - int Number of send bytes
        Output:
            out_buf - ctypes.c_ubyte list List with fc23 header pre-made plus 
                      length bytes"""
        modbus_bytes = length + 11
        # Words to write starting from [17]
        aebus_words = (length + 3) // 2
        # Bytes to write starting from [17]
        aebus_bytes = aebus_words * 2
        out_buf = (ctypes.c_ubyte * (19 + length))(0, 0, 0, 0,
                   modbus_bytes // 256, modbus_bytes % 256, 0, 23, 255, 255, 0,
                   0, 255, 255, 0, aebus_words, aebus_bytes, cmd, length)
        return out_buf
        
    def connect_serial(self, com, baud=AEBUS_DEFAULT_BAUD, timeout=1000, addr=1):
        """Connect to serial port via AEBus Gateway
        Input:
            com - string Comport string such as 'COM1'
            baud - int Baud rate number
                   Default: 19200
            timeout - int AEBus timeout
                      Default: 1000
            addr - int AEBus Address number
                   Default: 1"""
        com += '\0'
        self.create_buffer_cmd(0, '1:1,3:4,6:s', '1:4', 3, baud, timeout,
                               addr, com)
    
    def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args): 
        """Create a send buffer and return formatted receive buffer  
        Input:
            cmd - int AEBus command number
            send_sizes - str Format string to send (see create_buffer_cmd Documentation.doc
                         for more details)
            recv_sizes - str Format string to receive (see create_buffer_cmd Documentation.doc
                         for more details)
            *args - Dynamic arugments of any type to send
        Output:
            vals - list List containing formatted output
        Note:
            Make sure to use connect_serial method beforehand, otherwise you 
            will get an error message.
            This also uses FC23 and not FC100, there is not support for FC100 
            with the AEBus Gateway so far."""
        start = time.time()
        while time.time() - start < 5:
            buf, length = create_buffer(send_sizes, args)
            vals = []
            out_buf = self.__fc23_packet(cmd, length)
            
            for x in range(19, 19 + length):
                out_buf[x] = buf[x - 19]
                
            # send the packet
            self.sock.sendall(out_buf)
            
            # read response
            try:
                in_buf = self.sock.recv(261)
                vals.append(0)
                # For a case where user wants a string returned, but a csr was returned
                # Some values for a csr would crash the program because of Unicode 
                # encoding doesn't support that value
                if ':s' in recv_sizes and len(in_buf) == 12:
                    vals.append(0)
                    vals.append(in_buf[11])
                # For all commands, if recv_sizes are 0:0 then a csr will be returned
                # if one was returned
                elif recv_sizes == '0:0':
                    if len(in_buf) == 12 and in_buf[11] != 0:
                        vals.append(0)
                    else:
                        vals.append(len(in_buf) - 11)
                    vals.append(in_buf[11])
                # For set commands, if csr is not 0, then a csr is returned
                elif cmd < 128 and in_buf[11] != 0:
                    vals.append(0)
                    vals.append(in_buf[11])
                # For report commands that aren't '1:1', if 1 byte was received, then 
                # a csr was returned
                elif cmd >= 128 and recv_sizes != '1:1' and len(in_buf) == 12:
                    vals.append(0)
                    vals.append(in_buf[11])
                # For report commands that are '1:1', if nothing was returned, then 
                # there was an error
                elif cmd >= 128 and recv_sizes == '1:1' and len(in_buf) == 11:
                    vals.append(0)
                    vals.append('Error: No data received')
                else:
                    vals.append(len(in_buf) - 11)
                    vals = create_return(vals, recv_sizes, len(in_buf) - 11, in_buf[11:])
                return vals
            except Exception as e:
                _e = e
                # try reconnecting if a socket crashed, i.e., generator was shut
                # down or resetted
                self.sock.close()
                self.__init__(self.ip_address, self.port)
        #If number of command attempts exceeds 100
        raise Exception("Ethernet Command Error:", _e)
                
    def close(self):
        """Close the socket to AEBus Gateway"""
        self.sock.close()
        
        
class AEUDP(object):
    """Create a UDP socket
    Input:
        ip_address - str The IP address of the fixture/generator
        port - int UDP port number
            Default: 502"""
    def __init__(self, addr, ip_address, port=502):
        """Create a UDP socket
        Input:
            ip_address - str The IP address of the fixture/generator
            port - int UDP port number
                Default: 502"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ip_address = ip_address
        self.port = port
        self.addr = addr
        
    def create_buffer_cmd(self, cmd, send_sizes, recv_sizes, *args):
        """Create a send buffer and return formatted receive buffer  
        Input:
            cmd - int AEBus command number
            send_sizes - str Format string to send (see create_buffer_cmd Documentation.doc
                         for more details)
            recv_sizes - str Format string to receive (see create_buffer_cmd Documentation.doc
                         for more details)
            *args - Dynamic arugments of any type to send
        Output:
            vals - list List containing formatted output"""
        buf, length = create_buffer(send_sizes, args)
        vals = []
        modbus_bytes = length + 6
        # modbus arguments, first 4 bytes are 0s, 5th argument is modbus
        # bytes / 256, 6th argument is modbus bytes % 256, 7th argument is 
        # address, 8th argument is fc version, 9th agrument is cmd, 10th 
        # argument is reserved, 11th agrument is the length, and last
        # argument is 0.
        out_buf = (ctypes.c_ubyte * (12 + length))(0, 0, 0, 0, 
                   int(modbus_bytes) // 256, int(modbus_bytes) % 256, 
                   int(self.addr), 100, cmd, 0, int(length), 0)
        
        #create a byte buffer to pack into out_buf
        byte_buf = bytearray(buf)
        for x in range(12, 12 + length):
            out_buf[x] = byte_buf[x - 12]
        
        # send the packet
        self.sock.sendto(out_buf, (self.ip_address, self.port))
            
        # read response
        in_buf, server_address = self.sock.recvfrom(261)
        # append AEBus error code
        vals.append(int(in_buf[7]) & 0x80)
        # append received bytes
        vals.append(len(in_buf) - 12)
        # get the csr from the packet
        if recv_sizes == '1:1' and len(in_buf) == 12:
            # Append the csr
            vals.append(in_buf[9])
        elif recv_sizes == '0:0':
            if in_buf[9] != 0:
                vals.append(in_buf[9])
            else:
                vals.append(in_buf[12])
        else:
            # If there is a csr insert the csr to the buffer
            if in_buf[9] != 0:
                vals.append(in_buf[9])
            else:
                vals = create_return(vals, recv_sizes, len(in_buf) - 13, in_buf[12:])
        return vals
        
    def close(self):
        """Close the socket"""
        self.sock.close()
        

class ASCIIPort(object):
    """Create an ASCII communication object
    Input:
        comport - str Comport formatted string e.g. COM1
        baud - int The baud rate of the fixture/generator
            Default: 115200
        timeout - int AE comport timeout value
            Default: 1000"""
    def __init__(self, comport, baud=AEBUS_FAST_BAUD, timeout=1000):
        """Create an ASCII communication object
        Input:
            comport - str Comport formatted string e.g. COM1
            baud - int The baud rate of the fixture/generator
                Default: 115200
            timeout - int AE comport timeout value
                Default: 1000"""
        # This is for consistency sake, Serial only needs the number, not the
        # string
        com_num = int(comport[3:]) - 1   # Serial is zero based
        # Timeout is in secs, not millisecs
        self.device = serial.Serial(com_num, baud, timeout=timeout / 1000)
        
    def read(self, addr):
        """Send an address and read the data returned
        Input:
            addr - str Address string formatted as either 0x004 or 004
        Output:
            buf - list List containing data received
            Nothing if operation failed"""
        # Check if the address is 3 bytes
        if len(addr) != 3 and len(addr) != 5:
            print('Error: Expected 3 characters, received {} characters.  Data '
                  'was not sent'.format(len(addr)))
            return
        # If addr was sent in as a hex string, format to send
        if '0x' in addr:
            addr = addr[2:]
        
        # Merge R and addr together and convert to ASCII numbers
        buf = 'R' + addr + '\r\n'
        num_buf = [ord(c) for c in buf]
        self.device.write(num_buf)
        ret = self.device.read(5)
        ret_buf = '0x' + ret.decode('utf-8')
        # Cut off the new line character
        return ret_buf[:6]
        
    def write(self, addr, data):
        """Write an address
        *** WARNING: This method does not read modify write.  This will 
        overwrite the current register. ***
        Input:
            addr - str Address string formatted as either 0x004 or 004
            data - int Integer containing data to send (Up to 2 bytes)"""
        # Check if the address is 3 bytes
        if len(addr) != 3 and len(addr) != 5:
            print('Address error: Expected 3 characters, received {} characters.'
                  '  Data was not sent'.format(len(addr)))
            return
        # If addr was sent in as a hex string, format to send
        if '0x' in addr:
            addr = addr[2:]
        
        # Check if data fits in a 2 byte variable
        if data > 0xFFFF:
            print('Data error: Expected a 2 byte integer, received a bigger value.'
                  '  Value was not sent.')
            return
        # Convert int to hex (without the 0x header)
        data = '{:04x}'.format(data)

        # Merge W, addr, and data together and convert to ASCII numbers
        buf = 'W' + addr + data + '\r\n'
        # Convert ASCII values to bytes
        num_buf = [ord(c) for c in buf]
        self.device.write(num_buf)
        
    def close(self):
        """Close the comport"""
        self.device.close()
        
        
class ACOnOffFixture(object):
    """Create a class variable for parallel port
    Input:
        parallel_port - int Parallel port number
            Default: 0x378
    Note:
    This class requires the DLPORTIO dll"""
    def __init__(self, parallel_port=0x378):
        """Create a class variable for parallel port
        Input:
            parallel_port - int Parallel port number
                Default: 0x378
        Note:
        This class requires the DLPORTIO dll"""
        self.port = parallel_port
        self.dll = ctypes.windll.DLPORTIO
        
    def on_off(self, on_off):
        """Turn on or off the AC box
        Input:
            on_off - int 0 for off or 1 for on"""
        if on_off == 1:
            self.dll.DlPortWritePortUchar(self.port, on_off)
        elif on_off == 0:
            self.dll.DlPortWritePortUchar(self.port, on_off)
        else:
            raise Exception('Invalid paramenter sent in, must be 0 or 1')
            
        
def create_buffer(send_sizes, args):
    """Create the send buffer
    Input:
        send_sizes - str Format string to send
        args - tuple Data stored in a tuple
    Output:
        master_buf - bytearray Byte array containing buffer in bytes
        total_bytes_sent - int Total bytes counted to be sent"""
    master_buf = bytearray()
    pos = 0
    total_bytes_sent = 0
    
    # split the strings to get data types
    send_num_of_vars = send_sizes.split(',')
    
    # From string, prepare the buffer by finding the various options    
    for data_type in send_num_of_vars:
        # Split the string by : to find num of data types and data type number
        split_data_type = data_type.split(':')
        sec_arg = split_data_type[1]
        # Special case of signed variable, e.g. 3:4d (d must be at the end)
        if sec_arg[-1].lower() == 'd':
            if int(sec_arg[0]) == 0:
                continue
            elif int(sec_arg[0]) == 1:
                chunk = (ctypes.c_byte * int(split_data_type[0]))()
                for num_arg in range(int(split_data_type[0])):
                    chunk[num_arg] = args[pos]
                    pos += 1
                    total_bytes_sent += int(sec_arg[0])
            elif int(sec_arg[0]) == 2:
                chunk = (ctypes.c_int16 * int(split_data_type[0]))()
                for num_arg in range(int(split_data_type[0])):
                    chunk[num_arg] = args[pos]
                    pos += 1
                    total_bytes_sent += int(sec_arg[0])
            elif int(sec_arg[0]) == 4:
                chunk = (ctypes.c_int32 * int(split_data_type[0]))()
                for num_arg in range(int(split_data_type[0])):
                    chunk[num_arg] = args[pos]
                    pos += 1
                    total_bytes_sent += int(sec_arg[0])
            elif int(sec_arg[0]) == 8:
                chunk = (ctypes.c_int64 * int(split_data_type[0]))()
                for num_arg in range(int(split_data_type[0])):
                    chunk[num_arg] = args[pos]
                    pos += 1
                    total_bytes_sent += int(sec_arg[0])
            else:
                raise Exception('Unknown byte value! (Supported: 1, 2, 4, and 8 ' 
                'bytes)')
        
        # Special case s for string, sends a string, but only send one argument
        # instead of n arguments, e.g. 25:s            
        elif sec_arg[-1].lower() == 's':
            chunk = (ctypes.c_ubyte * int(split_data_type[0]))()
            cur_arg = args[pos]
            for chr_arg in range(int(split_data_type[0])):
                if chr_arg < len(cur_arg):
                    chunk[chr_arg] = ord(cur_arg[chr_arg])
                    total_bytes_sent += 1
            pos += 1
        # Special case b for byte array, send n bytes, but in an array
        elif sec_arg[-1].lower() == 'b':
            chunk = (ctypes.c_ubyte * int(split_data_type[0]))()
            cur_arg = args[pos]
            for byte_arg in range(int(split_data_type[0])):
                chunk[byte_arg] = cur_arg[byte_arg]
                total_bytes_sent += 1
            pos += 1
        # Special case f for floats, e.g. 2:f
        elif sec_arg[-1].lower() == 'f':
            chunk = (ctypes.c_float * int(split_data_type[0]))()
            for num_arg in range(int(split_data_type[0])):
                chunk[num_arg] = args[pos]
                pos += 1
                total_bytes_sent += 4
        elif int(sec_arg) == 0:
            continue
        elif int(sec_arg) == 1:
            chunk = (ctypes.c_ubyte * int(split_data_type[0]))()
            for num_arg in range(int(split_data_type[0])):
                chunk[num_arg] = args[pos]
                pos += 1
                total_bytes_sent += int(sec_arg)
        elif int(sec_arg) == 2:
            chunk = (ctypes.c_uint16 * int(split_data_type[0]))()
            for num_arg in range(int(split_data_type[0])):
                chunk[num_arg] = args[pos]
                pos += 1
                total_bytes_sent += int(sec_arg)
        elif int(sec_arg) == 4:
            chunk = (ctypes.c_uint32 * int(split_data_type[0]))()
            for num_arg in range(int(split_data_type[0])):
                chunk[num_arg] = args[pos]
                pos += 1
                total_bytes_sent += int(sec_arg)
        elif int(sec_arg) == 8:
            chunk = (ctypes.c_uint64 * int(split_data_type[0]))()
            for num_arg in range(int(split_data_type[0])):
                chunk[num_arg] = args[pos]
                pos += 1
                total_bytes_sent += int(sec_arg)
        else:
            raise Exception('Unknown byte value! (Supported: 1, 2, 4, and 8 ' 
            'bytes)')
                
        byte_chunk = bytearray(chunk)
        master_buf.extend(byte_chunk)
        
    # pad extra bytes if buffer is < than 255
    if 255 > len(master_buf):
        pad = bytearray(255 - len(master_buf))
        master_buf.extend(pad)
    return master_buf, total_bytes_sent
    
def create_return(return_vals, recv_sizes, bytes_read, buf):
    """Get return values specified by recv_sizes and return a predefined 
    list with all values entered"""
    recv_num_of_vars = recv_sizes.split(',')
    pos = 0
    
    for data_type in recv_num_of_vars:
        split_data_type = data_type.split(':')
        sec_arg = split_data_type[1]
        ctr = 0
        # find the data type, create a temp buf for each data type and cast it
        # to the appropiate data type
        
        # signed format special case, requires standard string format, but with
        # a d at end, e.g., '2:4d' for 2 4 byte signed ints
        if sec_arg[-1].lower() == 'd':
            if int(sec_arg[0]) == 0:
                continue
            elif int(sec_arg[0]) == 1:
                while ctr < int(split_data_type[0]) and pos <= bytes_read:
                    return_vals.append(buf[pos])
                    pos += 1
                    ctr += 1
            elif int(sec_arg[0]) == 2:
                while ctr < int(split_data_type[0]) and pos <= bytes_read:
                    tmp_buf = (ctypes.c_byte * 2)(*buf[pos:pos + 2])
                    val = ctypes.cast(ctypes.pointer(tmp_buf), 
                                      ctypes.POINTER(ctypes.c_int16)).contents.value
                    return_vals.append(val)
                    pos += 2
                    ctr += 1
            elif int(sec_arg[0]) == 4:
                while ctr < int(split_data_type[0]) and pos <= bytes_read:
                    tmp_buf = (ctypes.c_byte * 4)(*buf[pos:pos + 4])
                    val = ctypes.cast(ctypes.pointer(tmp_buf), 
                                      ctypes.POINTER(ctypes.c_int32)).contents.value
                    return_vals.append(val)
                    pos += 4
                    ctr += 1
            elif int(sec_arg[0]) == 8:
                while ctr < int(split_data_type[0]) and pos <= bytes_read:
                    tmp_buf = (ctypes.c_byte * 8)(*buf[pos:pos + 8])
                    val = ctypes.cast(ctypes.pointer(tmp_buf), 
                                      ctypes.POINTER(ctypes.c_int64)).contents.value
                    return_vals.append(val)
                    pos += 8
                    ctr += 1
            elif int(sec_arg) > 8:
                while ctr < int(split_data_type[0]) and pos + int(sec_arg) <= bytes_read:
                    # Just store the raw bytes as a bytes object
                    val = bytes(buf[pos:pos + int(sec_arg)])
                    return_vals.append(val)
                    pos += int(sec_arg)
                    ctr += 1
            else:
                raise Exception('Unknown byte value! (Supported: 1, 2, 4, and 8 ' 
                'bytes)')
                
        # float format special case, requires (number of floats):f
        elif sec_arg[-1].lower() == 'f':
            while ctr < int(split_data_type[0]) and pos <= bytes_read:
                tmp_buf = (ctypes.c_byte * 4)(*buf[pos:pos + 4])
                val = ctypes.cast(ctypes.pointer(tmp_buf), 
                                  ctypes.POINTER(ctypes.c_float)).contents.value
                return_vals.append(val)
                pos += 4
                ctr += 1
        # special case if you are only expected a string, but don't know the 
        # string size, e.g. cmd 128. have s:s as string format
        elif split_data_type[0].lower() == 's':
            return_vals.append(str(buf, errors='ignore').split('\0')[0])
            pos = bytes_read + 1
        # string format special case, requires (length of string):s
        elif sec_arg[-1].lower() == 's':
            tmp_buf = (ctypes.c_char * int(split_data_type[0]))()
            while ctr < int(split_data_type[0]) and pos <= bytes_read:
                tmp_buf[ctr] = buf[pos]
                pos += 1
                ctr += 1
            return_vals.append(str(tmp_buf, errors='ignore').split('\0')[0])
        elif int(sec_arg) == 0:
            continue
        elif int(sec_arg) == 1:
            while ctr < int(split_data_type[0]) and pos <= bytes_read:
                return_vals.append(buf[pos])
                pos += int(sec_arg)
                ctr += 1
        elif int(sec_arg) == 2:
            while ctr < int(split_data_type[0]) and pos <= bytes_read:
                tmp_buf = (ctypes.c_ubyte * 2)(*buf[pos:pos + 2])
                val = ctypes.cast(ctypes.pointer(tmp_buf), 
                                  ctypes.POINTER(ctypes.c_uint16)).contents.value
                return_vals.append(val)
                pos += int(sec_arg)
                ctr += 1
        elif int(sec_arg) == 4:
            while ctr < int(split_data_type[0]) and pos <= bytes_read:
                tmp_buf = (ctypes.c_ubyte * 4)(*buf[pos:pos + 4])
                val = ctypes.cast(ctypes.pointer(tmp_buf), 
                                  ctypes.POINTER(ctypes.c_uint32)).contents.value
                return_vals.append(val)
                pos += int(sec_arg)
                ctr += 1
        elif int(sec_arg) == 8:
            while ctr < int(split_data_type[0]) and pos <= bytes_read:
                tmp_buf = (ctypes.c_ubyte * 8)(*buf[pos:pos + 8])
                val = ctypes.cast(ctypes.pointer(tmp_buf), 
                                  ctypes.POINTER(ctypes.c_uint64)).contents.value
                return_vals.append(val)
                pos += int(sec_arg)
                ctr += 1
        elif int(sec_arg) > 8:
            while ctr < int(split_data_type[0]) and pos + int(sec_arg) <= bytes_read:
                # Just store the raw bytes as a bytes object
                val = bytes(buf[pos:pos + int(sec_arg)])
                return_vals.append(val)
                pos += int(sec_arg)
                ctr += 1
        else:
            raise Exception('Unknown byte value! (Supported: 1, 2, 4, and 8 or more' 
            'bytes)')

    print(return_vals)
    return return_vals
    
