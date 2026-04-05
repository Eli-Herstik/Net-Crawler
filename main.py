"""Main entry point for API mapping system."""
import asyncio
import json
import argparse
import logging
import sys
from pathlib import Path
from config_loader import load_config
from api_mapper import APIMapper

logger = logging.getLogger(__name__)


async def main():
    """Main function."""
    parser = argparse.ArgumentParser(description='API Mapping System')
    parser.add_argument('--config', '-c', required=True, help='Path to configuration JSON file')
    parser.add_argument('--output', '-o', help='Output file path (overrides config)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose (DEBUG) logging')

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )

    # Load configuration
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Configuration file not found: %s", config_path)
        sys.exit(1)

    try:
        config = load_config(str(config_path))
        logger.info("Configuration loaded successfully")
    except Exception as e:
        logger.error("Error loading configuration: %s", e)
        sys.exit(1)

    # Override output file if provided
    if args.output:
        config.output_file = args.output

    # Create mapper
    mapper = APIMapper(config)

    try:
        # Initialize
        await mapper.initialize()
        logger.info("Browser initialized")

        # Run mapping
        result = await mapper.map_website()

        # Save output
        output_path = Path(config.output_file)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        logger.info("Mapping complete!")
        logger.info("Found %d unique api calls", len(result.get('api_calls', [])))
        logger.info("Results saved to: %s", output_path)

    except KeyboardInterrupt:
        logger.info("Mapping interrupted by user")
    except Exception as e:
        logger.exception("Error during mapping: %s", e)
        sys.exit(1)
    finally:
        await mapper.cleanup()
        logger.info("Cleanup complete")


if __name__ == '__main__':
    asyncio.run(main())

