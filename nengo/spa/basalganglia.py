import nengo
from .. import objects

from .rules import Rules
from .base import Module


class BasalGanglia(nengo.networks.BasalGanglia, Module):
    def make(self, rules, input_filter=0.002):
        self.rules = Rules(rules)
        self.input_filter = input_filter
        
        nengo.networks.BasalGanglia.make(self, dimensions=self.rules.count) 
   
    def on_add(self, spa):
        Module.on_add(self, spa)
        
        self.rules.process(spa)
        
        for input, transform in self.rules.get_inputs().iteritems():
            nengo.Connection(input, self.input, 
                                transform=transform, 
                                filter=self.input_filter)
    
            
        
        
        

