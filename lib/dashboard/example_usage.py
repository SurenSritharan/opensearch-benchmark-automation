#!/usr/bin/env python3
"""
Example usage of the refactored dashboard generation modules.
Demonstrates how to use the new modular dashboard system with multi-scenario support.
"""
from pathlib import Path
from lib.dashboard import DashboardOrchestrator, generate_dashboards


def example_basic_usage():
    """Basic usage example - simplest way to generate dashboards."""
    print("=== Basic Usage Example ===")
    
    # Method 1: Use convenience function
    generate_dashboards(Path("results/20260616-143540"))
    
    print("✓ Dashboards generated successfully!")


def example_orchestrator_usage():
    """Example using orchestrator directly."""
    print("\n=== Orchestrator Usage Example ===")
    
    # Initialize orchestrator
    orchestrator = DashboardOrchestrator(
        results_dir=Path("results/20260616-143540")
    )
    
    # Generate all dashboards
    orchestrator.generate_all()
    
    print("✓ Dashboards generated!")


def example_accessing_data():
    """Example of accessing collected data."""
    print("\n=== Accessing Data Example ===")
    
    orchestrator = DashboardOrchestrator(
        results_dir=Path("results/20260616-143540")
    )
    
    # Collect data
    all_data = orchestrator.collector.collect_data()
    
    # Show structure
    print("\nData structure for each engine:")
    for engine, engine_data in all_data.items():
        print(f"\n{engine}:")
        scenarios = engine_data.get('scenarios', {})
        print(f"  Total scenarios: {len(scenarios)}")
        
        for scenario_name, scenario_info in scenarios.items():
            scenario_type = scenario_info['type']
            sweep_count = len(scenario_info.get('sweeps', []))
            print(f"    - {scenario_name} (type: {scenario_type}, sweeps: {sweep_count})")


def example_multi_scenario_support():
    """
    Example demonstrating multi-scenario support.
    
    This shows how the system handles multiple scenarios of the same type,
    such as scenario-1-search, scenario-2-search, scenario-3-search.
    """
    print("\n=== Multi-Scenario Support Example ===")
    
    # Example data structure (what you'd get from collect_data())
    example_data = {
        'jvector': {
            'scenarios': {
                'scenario-1-search': {
                    'type': 'search',
                    'sweeps': ['sweep-1', 'sweep-2'],
                    'data': {
                        'sweep-1': {'recall': 0.95, 'latency_p99': 45.2},
                        'sweep-2': {'recall': 0.98, 'latency_p99': 52.1}
                    }
                },
                'scenario-2-search': {
                    'type': 'search',
                    'sweeps': ['sweep-1'],
                    'data': {
                        'sweep-1': {'recall': 0.92, 'latency_p99': 38.5}
                    }
                },
                'scenario-1-bulk-ingest': {
                    'type': 'bulk_ingest',
                    'sweeps': ['sweep-1'],
                    'data': {
                        'sweep-1': {'throughput': 15000, 'indexing_time': 120.5}
                    }
                }
            }
        },
        'faiss': {
            'scenarios': {
                'scenario-1-search': {
                    'type': 'search',
                    'sweeps': ['sweep-1', 'sweep-2'],
                    'data': {
                        'sweep-1': {'recall': 0.93, 'latency_p99': 42.8},
                        'sweep-2': {'recall': 0.96, 'latency_p99': 48.3}
                    }
                },
                'scenario-2-search': {
                    'type': 'search',
                    'sweeps': ['sweep-1'],
                    'data': {
                        'sweep-1': {'recall': 0.90, 'latency_p99': 35.2}
                    }
                }
            }
        }
    }
    
    print("\nExample data structure:")
    print(f"Engines: {list(example_data.keys())}")
    
    for engine, engine_data in example_data.items():
        print(f"\n{engine} scenarios:")
        for scenario_name, scenario_info in engine_data['scenarios'].items():
            print(f"  - {scenario_name} ({scenario_info['type']})")
    
    print("\nKey benefits:")
    print("  ✓ Unlimited scenarios of the same type")
    print("  ✓ Each scenario gets its own comparison dashboard")
    print("  ✓ Main dashboard lists all scenarios grouped by type")
    print("  ✓ No data conflicts or overwrites")


def example_scenario_detection():
    """Example showing how scenarios are automatically detected."""
    print("\n=== Scenario Detection Example ===")
    
    orchestrator = DashboardOrchestrator(
        results_dir=Path("results/20260616-143540")
    )
    
    # Collect data first
    orchestrator.all_data = orchestrator.collector.collect_data()
    
    # Detect scenarios
    scenarios = orchestrator._detect_scenarios()
    
    print("\nDetected scenarios:")
    for scenario_name, scenario_type in scenarios.items():
        print(f"  - {scenario_name} (type: {scenario_type})")
    
    print("\nGenerated dashboards will be:")
    print("  - index.html (main overview)")
    for scenario_name in scenarios.keys():
        print(f"  - {scenario_name}-comparison.html")


def example_migration_guide():
    """
    Guide for migrating from old to new data structure.
    
    OLD STRUCTURE (single scenario per type):
    {
        'jvector': {
            'search_sweeps': ['sweep-1', 'sweep-2'],
            'search': {...},
            'bulk_ingest': {...}
        }
    }
    
    NEW STRUCTURE (multiple scenarios per type):
    {
        'jvector': {
            'scenarios': {
                'scenario-1-search': {
                    'type': 'search',
                    'sweeps': ['sweep-1', 'sweep-2'],
                    'data': {...}
                },
                'scenario-2-search': {
                    'type': 'search',
                    'sweeps': ['sweep-1'],
                    'data': {...}
                }
            }
        }
    }
    """
    print("\n=== Migration Guide ===")
    print("\nOLD way (limited to one scenario per type):")
    print("  all_data[engine]['search_sweeps']")
    print("  all_data[engine]['search']")
    
    print("\nNEW way (unlimited scenarios):")
    print("  all_data[engine]['scenarios']['scenario-1-search']['sweeps']")
    print("  all_data[engine]['scenarios']['scenario-1-search']['data']")
    
    print("\nBenefits of new structure:")
    print("  ✓ Support multiple scenarios of same type")
    print("  ✓ Clear scenario naming and organization")
    print("  ✓ Easier to add new scenario types")
    print("  ✓ Better separation of concerns")


def example_custom_workflow():
    """Example of custom workflow with individual components."""
    print("\n=== Custom Workflow Example ===")
    
    from lib.dashboard.data_collector import DataCollector
    from lib.dashboard.main_dashboard import MainDashboardGenerator
    from lib.dashboard.scenario_comparison import ScenarioComparisonGenerator
    from datetime import datetime
    
    results_dir = Path("results/20260616-143540")
    
    # Step 1: Collect data
    print("Step 1: Collecting data...")
    collector = DataCollector(results_dir, "msmarco")
    all_data = collector.collect_data()
    
    # Step 2: Generate main dashboard
    print("Step 2: Generating main dashboard...")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    main_gen = MainDashboardGenerator("msmarco", timestamp)
    main_html = main_gen.generate(all_data)
    
    with open(results_dir / "index.html", 'w') as f:
        f.write(main_html)
    
    # Step 3: Generate scenario dashboards
    print("Step 3: Generating scenario dashboards...")
    for engine_data in all_data.values():
        for scenario_name, scenario_info in engine_data.get('scenarios', {}).items():
            scenario_type = scenario_info['type']
            
            scenario_gen = ScenarioComparisonGenerator(
                "msmarco",
                timestamp,
                scenario_name,
                scenario_type,
                results_dir
            )
            
            scenario_html = scenario_gen.generate(all_data)
            if scenario_html:
                filename = f"{scenario_name}-comparison.html"
                with open(results_dir / filename, 'w') as f:
                    f.write(scenario_html)
                print(f"  ✓ Created {filename}")
            
            break  # Only do first scenario for example
        break  # Only do first engine for example
    
    print("✓ Custom workflow completed!")


if __name__ == "__main__":
    print("Dashboard Generation Module - Usage Examples")
    print("=" * 60)
    
    # Run examples (comment out the ones that require actual data)
    print("\nNote: Some examples require actual benchmark results data.")
    print("Showing conceptual examples only...\n")
    
    # These don't require actual data
    example_multi_scenario_support()
    example_migration_guide()
    
    # These would require actual data - showing structure only
    print("\n=== Examples requiring actual data ===")
    print("To run these, ensure you have benchmark results in the specified directory:")
    print("  - example_basic_usage()")
    print("  - example_orchestrator_usage()")
    print("  - example_accessing_data()")
    print("  - example_scenario_detection()")
    print("  - example_custom_workflow()")
    
    print("\n" + "=" * 60)
    print("Examples completed!")
    print("\nFor more information, see:")
    print("  - README.md - Module documentation")
    print("  - REFACTORING_SUMMARY.md - Refactoring overview")
    print("  - MULTI_SCENARIO_IMPLEMENTATION.md - Multi-scenario details")

# Made with Bob
