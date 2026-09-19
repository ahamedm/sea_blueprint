#!/usr/bin/env python3
"""
Quick test to verify the Strands Agent integration works.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from agents.knowledge_extraction import create_knowledge_extraction_agent

def main():
    print("Creating Knowledge Extraction Agent...")
    agent = create_knowledge_extraction_agent()
    print("✓ Agent created")
    
    print("\nTesting with simple input...")
    test_input = {
        "document": "The Payment Gateway Platform shall accept payment requests.",
        "document_type": "requirements",
        "domain": "payment_processing"
    }
    
    try:
        result = agent.run(test_input)
        
        if result.success:
            print("✓ Agent execution successful")
            print(f"\nExtracted {len(result.output.get('triples', []))} triples")
            print(f"Confidence: {result.confidence:.2%}")
            
            # Show sample triples
            triples = result.output.get('triples', [])
            if triples:
                print("\nSample triples:")
                for i, triple in enumerate(triples[:3], 1):
                    print(f"  {i}. {triple['subject']} --{triple['predicate']}--> {triple['object']}")
                    print(f"     Confidence: {triple['confidence']:.2%}")
        else:
            print("✗ Agent execution failed")
            print(f"Errors: {result.errors}")
            
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
