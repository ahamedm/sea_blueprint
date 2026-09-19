#!/usr/bin/env python3
"""
Quick test script to verify the SEA agent framework setup.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


def test_knowledge_extraction():
    """Test the Knowledge Extraction Agent."""
    print("\n" + "="*60)
    print("Testing Knowledge Extraction Agent")
    print("="*60 + "\n")
    
    try:
        from agents.knowledge_extraction import create_knowledge_extraction_agent
        
        # Create agent
        print("Creating agent...")
        agent = create_knowledge_extraction_agent()
        print("✓ Agent created successfully")
        
        # Test input
        test_input = {
            "document": """
            The Payment Gateway Platform shall accept payment requests from storefronts.
            Each request must be mapped to a unique tenancy identifier.
            The system shall route transactions to the optimal PGSP based on country and currency.
            """,
            "document_type": "requirements",
            "domain": "payment_processing"
        }
        
        print("\nRunning agent on test input...")
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
            
            return True
        else:
            print("✗ Agent execution failed")
            print(f"Errors: {result.errors}")
            return False
            
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_evaluation_framework():
    """Test the evaluation framework setup."""
    print("\n" + "="*60)
    print("Testing Evaluation Framework")
    print("="*60 + "\n")
    
    try:
        from evaluation import SEAEvaluationFramework, create_evaluation_config
        
        # Create config
        print("Creating evaluation config...")
        config = create_evaluation_config(
            agent_name="knowledge_extraction",
            test_cases_path="data/test_cases/knowledge_extraction_test_cases.json",
            metrics=["answer_relevancy"]
        )
        print("✓ Evaluation config created")
        
        # Create framework
        print("Creating evaluation framework...")
        framework = SEAEvaluationFramework(config)
        print("✓ Evaluation framework created")
        
        # Load test cases
        print("Loading test cases...")
        test_cases = framework.load_test_cases()
        print(f"✓ Loaded {len(test_cases)} test cases")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_ontology():
    """Test ontology loading."""
    print("\n" + "="*60)
    print("Testing Ontology")
    print("="*60 + "\n")
    
    try:
        import yaml
        from pathlib import Path
        
        ontology_path = Path("ontology/requirements_base.yaml")
        
        if not ontology_path.exists():
            print(f"✗ Ontology file not found: {ontology_path}")
            return False
        
        print(f"Loading ontology from {ontology_path}...")
        with open(ontology_path, 'r') as f:
            ontology = yaml.safe_load(f)
        
        classes = list(ontology.get('classes', {}).keys())
        enums = list(ontology.get('enums', {}).keys())
        
        print(f"✓ Ontology loaded successfully")
        print(f"  Classes: {len(classes)}")
        print(f"  Enums: {len(enums)}")
        print(f"\nSample classes: {', '.join(classes[:10])}")
        
        return True
        
    except Exception as e:
        print(f"✗ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("SEA Platform - Setup Verification")
    print("="*60)
    
    results = []
    
    # Test 1: Ontology
    results.append(("Ontology", test_ontology()))
    
    # Test 2: Knowledge Extraction Agent
    results.append(("Knowledge Extraction Agent", test_knowledge_extraction()))
    
    # Test 3: Evaluation Framework
    results.append(("Evaluation Framework", test_evaluation_framework()))
    
    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60 + "\n")
    
    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    total_passed = sum(1 for _, passed in results if passed)
    total_tests = len(results)
    
    print(f"\nTotal: {total_passed}/{total_tests} tests passed")
    
    if total_passed == total_tests:
        print("\n🎉 All tests passed! Your SEA agent framework is ready.")
        print("\nNext steps:")
        print("  1. Copy .env.example to .env and add your API keys")
        print("  2. Run: sea-agent run --agent knowledge_extraction --input data/input/sample_requirements.md")
        print("  3. Run: sea-eval evaluate --agent knowledge_extraction --test-cases data/test_cases/knowledge_extraction_test_cases.json")
    else:
        print("\n⚠️  Some tests failed. Please check the errors above.")
    
    return total_passed == total_tests


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
