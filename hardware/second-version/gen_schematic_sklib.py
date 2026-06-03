from collections import defaultdict
from skidl import Pin, Part, Alias, SchLib, SKIDL, TEMPLATE

from skidl.pin import pin_types

SKIDL_lib_version = '0.0.1'

gen_schematic = SchLib(tool=SKIDL).add_parts(*[
        Part(**{ 'name':'L', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'L'}), 'ref_prefix':'L', 'fplist':[''], 'footprint':'Inductor_SMD:L_0805_2012Metric', 'keywords':'inductor choke coil reactor magnetic', 'description':'Inductor', 'datasheet':'', 'pins':[
            Pin(num='1',name='1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',name='2',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'C', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'C'}), 'ref_prefix':'C', 'fplist':[''], 'footprint':'Capacitor_SMD:C_0805_2012Metric', 'keywords':'cap capacitor', 'description':'Unpolarized capacitor', 'datasheet':'', 'pins':[
            Pin(num='1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'LP5907MFX-3.3', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'LP5907MFX-3.3'}), 'ref_prefix':'U', 'fplist':['Package_TO_SOT_SMD:SOT-23-5', 'Package_TO_SOT_SMD:SOT-23-5'], 'footprint':'Package_TO_SOT_SMD:SOT-23-5', 'keywords':'Single Output LDO Low-Noise', 'description':'250-mA Ultra-Low-Noise Low-IQ LDO, 3.3V, SOT-23', 'datasheet':'http://www.ti.com/lit/ds/symlink/lp5907.pdf', 'pins':[
            Pin(num='1',name='IN',func=pin_types.PWRIN),
            Pin(num='2',name='GND',func=pin_types.PWRIN),
            Pin(num='3',name='EN',func=pin_types.INPUT),
            Pin(num='4',name='NC',func=pin_types.NOCONNECT),
            Pin(num='5',name='OUT',func=pin_types.PWROUT)], 'unit_defs':[] }),
        Part(**{ 'name':'R', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'R'}), 'ref_prefix':'R', 'fplist':[''], 'footprint':'Resistor_SMD:R_0805_2012Metric', 'keywords':'R res resistor', 'description':'Resistor', 'datasheet':'', 'pins':[
            Pin(num='1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'SFH2440', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'SFH2440'}), 'ref_prefix':'D', 'fplist':['OptoDevice:Osram_BP104-SMD', 'OptoDevice:Osram_SFH2440'], 'footprint':'OptoDevice:Osram_SFH2440', 'keywords':'opto photodiode', 'description':'Silicon Photodiode, Vf 1V, Area 2.65x2.65mm', 'datasheet':'https://dammedia.osram.info/media/resource/hires/osram-dam-5467146/SFH%202440_EN.pdf', 'pins':[
            Pin(num='1',name='K',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',name='A',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'OPA1655DBV', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'OPA1655DBV'}), 'ref_prefix':'U', 'fplist':['Package_TO_SOT_SMD:SOT-23-5', 'Package_TO_SOT_SMD:SOT-23-5'], 'footprint':'Package_TO_SOT_SMD:SOT-23-5', 'keywords':'single opamp', 'description':'Single Ultra-Low-Noise, Low-Distortion, FET-Input, Burr-Brown Audio Operational Amplifiers, SOT-23-5', 'datasheet':'https://www.ti.com/lit/ds/symlink/opa1655.pdf', 'pins':[
            Pin(num='2',name='V-',func=pin_types.PWRIN),
            Pin(num='5',name='V+',func=pin_types.PWRIN),
            Pin(num='1',func=pin_types.OUTPUT,unit=1),
            Pin(num='3',name='+',func=pin_types.INPUT,unit=1),
            Pin(num='4',name='-',func=pin_types.INPUT,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'TMUX1208PW', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'TMUX1208PW'}), 'ref_prefix':'U', 'fplist':['Package_SO:TSSOP-16_4.4x5mm_P0.65mm', 'Package_SO:TSSOP-16_4.4x5mm_P0.65mm', 'Package_SO:TSSOP-16_4.4x5mm_P0.65mm'], 'footprint':'Package_SO:TSSOP-16_4.4x5mm_P0.65mm', 'keywords':'analog switch selector multiplexer', 'description':'5V, 8:1 General Purpose Analog Multiplexer, 5 Ohm Ron, 65 Mhz, TSSOP-16', 'datasheet':'https://www.ti.com/lit/ds/symlink/tmux1208.pdf', 'pins':[
            Pin(num='14',name='GND',func=pin_types.PWRIN),
            Pin(num='1',name='A0',func=pin_types.INPUT,unit=1),
            Pin(num='2',name='EN',func=pin_types.INPUT,unit=1),
            Pin(num='3',name='VSS',func=pin_types.PWRIN,unit=1),
            Pin(num='4',name='S1',func=pin_types.BIDIR,unit=1),
            Pin(num='5',name='S2',func=pin_types.BIDIR,unit=1),
            Pin(num='6',name='S3',func=pin_types.BIDIR,unit=1),
            Pin(num='7',name='S4',func=pin_types.BIDIR,unit=1),
            Pin(num='8',name='D',func=pin_types.BIDIR,unit=1),
            Pin(num='9',name='S8',func=pin_types.BIDIR,unit=1),
            Pin(num='10',name='S7',func=pin_types.BIDIR,unit=1),
            Pin(num='11',name='S6',func=pin_types.BIDIR,unit=1),
            Pin(num='12',name='S5',func=pin_types.BIDIR,unit=1),
            Pin(num='13',name='VDD',func=pin_types.PWRIN,unit=1),
            Pin(num='15',name='A2',func=pin_types.INPUT,unit=1),
            Pin(num='16',name='A1',func=pin_types.INPUT,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'MCP3201', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'MCP3201'}), 'ref_prefix':'U', 'fplist':[''], 'footprint':'Package_SO:MSOP-8_3x3mm_P0.65mm', 'keywords':'12-Bit Differential ADC SPI 1ch', 'description':'2.7V 12-Bit A/D Converter with SPIâ\x84¢ Serial Interface, PDIP-8/SOIC-8/MSOP-8/TSSOP-8', 'datasheet':'http://ww1.microchip.com/downloads/en/DeviceDoc/21290D.pdf', 'pins':[
            Pin(num='1',name='Vref',func=pin_types.PWRIN,unit=1),
            Pin(num='2',name='IN+',func=pin_types.INPUT,unit=1),
            Pin(num='3',name='IN-',func=pin_types.INPUT,unit=1),
            Pin(num='4',name='Vss',func=pin_types.PWRIN,unit=1),
            Pin(num='5',name='~{CS}/SHDN',func=pin_types.INPUT,unit=1),
            Pin(num='6',name='Dout',func=pin_types.OUTPUT,unit=1),
            Pin(num='7',name='CLK',func=pin_types.INPUT,unit=1),
            Pin(num='8',name='Vdd',func=pin_types.PWRIN,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'Conn_01x10', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'Conn_01x10'}), 'ref_prefix':'J', 'fplist':[''], 'footprint':'Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical', 'keywords':'connector', 'description':'Generic connector, single row, 01x10, script generated (kicad-library-utils/schlib/autogen/connector/)', 'datasheet':'', 'pins':[
            Pin(num='1',name='Pin_1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',name='Pin_2',func=pin_types.PASSIVE,unit=1),
            Pin(num='3',name='Pin_3',func=pin_types.PASSIVE,unit=1),
            Pin(num='4',name='Pin_4',func=pin_types.PASSIVE,unit=1),
            Pin(num='5',name='Pin_5',func=pin_types.PASSIVE,unit=1),
            Pin(num='6',name='Pin_6',func=pin_types.PASSIVE,unit=1),
            Pin(num='7',name='Pin_7',func=pin_types.PASSIVE,unit=1),
            Pin(num='8',name='Pin_8',func=pin_types.PASSIVE,unit=1),
            Pin(num='9',name='Pin_9',func=pin_types.PASSIVE,unit=1),
            Pin(num='10',name='Pin_10',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] }),
        Part(**{ 'name':'Conn_01x03', 'dest':TEMPLATE, 'tool':SKIDL, 'aliases':Alias({'Conn_01x03'}), 'ref_prefix':'J', 'fplist':[''], 'footprint':'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical', 'keywords':'connector', 'description':'Generic connector, single row, 01x03, script generated (kicad-library-utils/schlib/autogen/connector/)', 'datasheet':'', 'pins':[
            Pin(num='1',name='Pin_1',func=pin_types.PASSIVE,unit=1),
            Pin(num='2',name='Pin_2',func=pin_types.PASSIVE,unit=1),
            Pin(num='3',name='Pin_3',func=pin_types.PASSIVE,unit=1)], 'unit_defs':[] })])